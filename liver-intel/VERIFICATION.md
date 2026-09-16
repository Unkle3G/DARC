# Verification runbook

The implementation session had **no outbound network access** — every external
host (sec.gov, clinicaltrials.gov, the three newswires, fda.gov, ema.europa.eu,
hkexnews.hk, cninfo.com.cn) was refused at the egress proxy. So none of the
acceptance criteria in the task sheet could be executed, and nothing in this
repo should be read as "checked against the live service".

This is the runbook for the first run on a machine with network. Work it in task
order; each step ends with the acceptance bar from the handover.

## Step 0 — can this machine reach the sources at all?

```bash
export LIVER_INTEL_CONTACT="you@example.com"   # goes in the User-Agent; EDGAR requires it
python -m liver_intel.cli preflight
```

One line per host, fail-fast (no retries — it is a reachability probe). An HTTP
error counts as reachable: a 404 proves the connection got through, only a
refused tunnel does not.

If every host reports `blocked` or `unreachable`, that is the environment's
egress policy and no amount of engine work will fix it. Allow these hosts, then
start a **new session** — the proxy configuration is injected at container
start and does not hot-reload:

    www.globenewswire.com   www.businesswire.com   www.prnewswire.com
    www.sec.gov             data.sec.gov           clinicaltrials.gov
    api.fda.gov             www.fda.gov            www.accessdata.fda.gov
    www.ema.europa.eu       www.nmpa.gov.cn        www.cde.org.cn
    eutils.ncbi.nlm.nih.gov pubmed.ncbi.nlm.nih.gov
    www.hkexnews.hk         www.cninfo.com.cn

Only once preflight is green does the rest of this runbook mean anything.

```bash
python -m liver_intel.cli status               # baseline: 0 verified feeds is expected
```

---

## T1 — newswire company feeds

```bash
python -m liver_intel.cli discover --source newswire
python -m liver_intel.cli verify   --source T1
python -m liver_intel.cli status                     # prints roster coverage
python -m liver_intel.cli backfill --since $(date -d '30 days ago' +%F) --source newswire
```

1. Autodiscovery finds what the wire advertises in its HTML. Where a wire offers
   per-organization feeds through a subscription form rather than a link, work
   the form in a browser, then register what it produced:

   ```bash
   python -m liver_intel.cli feeds-add --id gnw.madrigal --source newswire --task T1 \
       --url "<the URL the form produced>" --company "Madrigal Pharmaceuticals"
   ```

   It lands as `unverified` and goes through exactly the same probe as a
   discovered one — hand-added is not trusted-added.
2. Add the keyword fallbacks (`newswire.FALLBACK_KEYWORDS`: hepatitis B, MASH,
   NASH, MASLD, primary biliary cholangitis, hepatocellular carcinoma, cirrhosis,
   hepatitis delta) as keyword feeds on each wire, same way.

**Acceptance:** `status` reports ≥80% roster coverage, and a 30-day backfill
matches a hand check of 10 releases against the companies' own newsrooms.
`verify` already enforces the "reachable, has a date field" half of the bar — a
feed without a parseable date is marked `dead`, not `verified`.

## T2 — SEC EDGAR 8-K

```bash
python -m liver_intel.cli verify   --source edgar
python -m liver_intel.cli backfill --since $(date -d '90 days ago' +%F) --source edgar
```

Confirm in the output that Madrigal, Akero, 89bio, Aligos, Vir and Mirum each
produced their 8-K filings, and that at least one Phase 3 result or termination
graded P0. If a company is missing, `status`/run notes will name it: the usual
cause is a ticker in the dictionary that does not match `company_tickers.json`.

**Watch for:** the exhibit lookup uses the filing folder's `index.json` and takes
the first `ex99*` document. Confirm against two real filings that this picks the
press release and not an exhibit index page.

## T3 — ClinicalTrials.gov

```bash
python -m liver_intel.cli verify   --source ctgov
python -m liver_intel.cli backfill --since $(date -d '30 days ago' +%F) --source ctgov
python -m liver_intel.cli backfill --since $(date -d '30 days ago' +%F) --source ctgov   # again
```

The second run is the regression the handover asks for: it must collect **0**
items, because the first run recorded every trial's state and nothing changed in
between. The state-change logic itself is already covered offline
(`tests/test_sources.py::test_last_update_only_change_produces_nothing`).

**Acceptance:** no-change entries do not reappear; a Phase 3
TERMINATED/SUSPENDED/WITHDRAWN with a non-empty `whyStopped` grades P0.

## T4 — regulators

```bash
python -m liver_intel.cli verify --source fda
python -m liver_intel.cli verify --source ema
python -m liver_intel.cli verify --source cn_regulator
python -m liver_intel.cli verify --source pubmed
```

- **FDA:** openFDA `drug/drugsfda` is seeded. The press-release RSS and the
  advisory-committee calendar must be **discovered and verified** — the previous
  version's FDA RSS URL was invented and 404'd, so do not reinstate one from
  memory. Add the calendar page as `"kind": "html"` and it becomes a change watch.
- **EMA:** find the live CHMP post-meeting highlights listing and the medicines
  data download; add both as registry entries.
- **NMPA / CDE:** add the four list pages (受理品种, 优先审评, 突破性治疗, 批件)
  as `"kind": "html"`, source `cn_regulator`. They are checked once a day. A
  block raises a manual-check note after the second consecutive failure and is
  never retried harder.

**Acceptance:** ≥5 FDA/EMA liver events backfilled for 2026, and NMPA/CDE list
changes detected reliably. Note that the first sighting of a list page only
records a baseline — a change is detectable from the second run onward.

## T5 — HKEX + CNINFO

Add the announcement-search endpoints for the roster's HK/A-share names
(腾盛博药, 歌礼, 正大天晴/中国生物制药, 海思科, 翰森, 东阳光, 特宝, 凯因 …) and
verify. The adapter expects JSON; if a venue answers HTML it says so in the run
notes rather than guessing at the shape.

**Acceptance:** 90-day backfill agrees with a spot check against the companies'
own announcement pages.

## T6 — conference calendar

Dates come from the society's own pages, not from recall. AASLD 2026 is filled
in: the meeting runs 5-9 November 2026 in Denver (`/tlm-26/event-registration`),
and the late-breaker date is the embargo lift, 5 November 2026
(`/tlm-26/embargo-policy`) -- the moment those abstracts become public and
therefore citable. Regular abstracts lift earlier, on 5 October.

EASL 2027 and APASL are still blank: the 2027 congress page carries no dates
yet, and APASL's domain is not in the environment's allowlist, so nothing could
be read. Both record where to look in `source_url`.

An unverified entry stays inert and `status` warns on each run. Verify with:

```bash
python -m liver_intel.cli status    # the warning disappears once dates are set
```

## T7 — newsroom watch

```bash
python -m liver_intel.cli discover --source newsroom
python -m liver_intel.cli verify   --source newsroom
```

Only needed for roster companies with no wire, no SEC and no exchange coverage;
the run notes name them. Prefer `sitemap.xml` — it carries `lastmod`, so it
produces real items rather than a bare "page changed".

## T8 — X alert layer

Leave disabled until T1–T5 are stable. Before enabling, check the current X API
tier and quota, then construct `XAlertSource(enabled=True, handles=[...])`.
Design rule already encoded: a post is only a trigger to fetch a first-party
document; `primary_links()` filters out the post itself and any media host.

---

## After the first full run

- `python -m liver_intel.cli status` — coverage, tracked NCTs, weekly pool size.
- Re-read the first daily report against the format of record
  (`liver_daily_2026-09-14.md`) and adjust `report.py` if the layout differs.
- Decide the weekly digest cap after watching one issue (`weekly --limit N`);
  it is deliberately unset.
- Re-run `verify --recheck` weekly — the crontab example already does.
