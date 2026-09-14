# 肝病情报引擎 liver-intel

Implementation of the v1 handover (2026-09-14). First-party sources only, every
endpoint verified before use, every graded item backed by quoted source text.

```bash
pip install -e ".[dev]"          # add ".[llm]" for the significance step
export LIVER_INTEL_CONTACT="you@example.com"   # EDGAR requires a contact UA

python -m liver_intel.cli status                 # registry / roster / calendar
python -m liver_intel.cli discover --source newswire
python -m liver_intel.cli verify   --source T1   # promotes candidates to verified
python -m liver_intel.cli daily                  # collect, grade, write the report
python -m liver_intel.cli weekly                 # Friday digest
```

## Read this first

Two things about this build:

1. **The attached files were not available.** `pharma_intel_v1.py`, `tagger_v1.py`,
   `domain_map_liver_v3.md` and `liver_daily_2026-09-14.md` were referenced by the
   handover but not attached to the implementation session. The dictionary
   (`data/domain_map_liver_v3.md`), the tagger and the report layout are therefore
   **rebuilt from the specification**, not ported. Each is structured so the real
   version drops in without touching the pipeline — see *Replacing the
   reconstructions* below.
2. **Nothing was verified against the live internet.** The session had no outbound
   network access (every host refused at the egress proxy), so no feed URL, no API
   response shape and no conference date in this repo has been confirmed against
   the real service. That is why the engine refuses to use an unverified endpoint
   instead of shipping URLs written from memory — the failure mode section 3 of
   the handover describes. `VERIFICATION.md` is the runbook for the first
   network-enabled run: it lists every acceptance test from the task sheet and
   how to execute it.

## Non-negotiables and where they live

| Constraint (section 0) | Enforced in |
| --- | --- |
| First-party sources only; no media, no preprints | `models.validate` rejects excluded hosts and any `src_kind` outside the allowed set |
| Every item carries its original URL | `models.validate` — an item without an http(s) URL cannot exist |
| Chinese content: translate and extract, never infer | `llm.SYSTEM_PROMPT` + `llm.assert_not_evaluative`; translations with judgement wording are stripped |
| P0 uncapped; P0+P1+P2 ≤ 10; P3 weekly only | `select.select_daily` |
| Significance decided with signals **and** source evidence | `llm._verify` drops any signal whose quotes are not verbatim in the document; `pipeline._attach_rule_evidence` does the same job for rule-fired signals |
| Auxiliary lines weigh 0.5 and never reach the daily alone; L13 weighs 1.0 | `config.line_weight`, `grade.grade`, `select.has_main_line` |

On the L13 exception: the handover reads 辅线权重 0.5，命中主线才可进每日；L13 例外为 1.0.
The exception is scoped to the **weight**, so L13 carries 1.0 but an L13-only item
still goes to the weekly digest. `test_l13_has_full_weight_but_stays_auxiliary`
and `test_auxiliary_only_items_go_to_the_weekly_pool` pin that reading; if you
meant L13 to bypass the main-line gate as well, change `select.has_main_line`.

## Task sheet → code

| Task | Status | Module | Notes |
| --- | --- | --- | --- |
| T1 newswire company feeds | built, **unverified** | `sources/newswire.py`, `discover.py`, `verify.py` | Feeds are discovered from the wire hosts and verified, never hard-coded. Keyword fallbacks in `newswire.FALLBACK_KEYWORDS`. Coverage measured by `cli status`. |
| T2 SEC EDGAR 8-K | built, **unverified** | `sources/edgar.py` | company_tickers → CIK → submissions → items 7.01/8.01/2.02 → EX-99.x body. Contact UA and ≤10 req/s enforced in `http.py` + `config.RATE_LIMITS`. |
| T3 ClinicalTrials.gov state change | built and tested offline | `sources/ctgov.py`, `store.NctState.diff` | Emits only when `overallStatus` / `resultsFirstPostDate` / `whyStopped` change. A bare LastUpdatePostDate move produces nothing. |
| T4 regulators | built, **unverified** | `sources/regulator.py`, `sources/pubmed.py` | openFDA drugsfda; FDA/EMA feeds and calendars come from the registry; NMPA/CDE are daily list-hash diffs that downgrade to a manual prompt on a block instead of retrying. |
| T5 HKEX + CNINFO | built, **unverified** | `sources/exchange.py` | Announcement-type filter on 自願公告 / 臨床試驗 / 藥品註冊. Endpoint shape must be confirmed — the adapter says so explicitly if the response is not JSON. |
| T6 conference windows | built, **dates not filled** | `conference.py`, `data/conferences.json` | EASL/AASLD/APASL entries ship with `verified: false` and are inert: no frequency change, no `conference` tag, and a warning every run until the dates are looked up. |
| T7 newsroom watch | built, **unverified** | `sources/newsroom.py` | Sitemap first, list-page hash second, only for companies T1/T2/T5 cannot reach (`newsroom.uncovered_companies`). |
| T8 X alert layer | stub, disabled | `sources/xalert.py` | `enabled=False`. `primary_links()` implements the "post is a hint, never content" rule. No credentials wired: the API tier still has to be checked. |

## Known pitfalls from the handover

- **Old RSS addresses (all 404).** None were carried over. The registry seeds
  discovery roots and documented API bases only, and `feeds.Registry.usable()`
  returns `verified` entries — so a collector with nothing verified collects
  nothing and says why, rather than 404ing in the dark.
- **Endpoints (403, media).** Dropped: `endpts.com` and the rest are in
  `models.EXCLUDED_HOST_SUBSTRINGS`, which `validate` applies to every item.
- **PubMed `sortpubdate` can be in the future.** `sources/pubmed.py` archives by
  collection date and keeps the publication date in `meta.pub_date`, flagging
  `pub_date_is_future` so the report can note it.
- **Tagger scope.** L6's disease-word gate and L5's cirrhosis fallback are
  implemented. The infection and digestive lines were **not** extended, as
  instructed.
- **Chinese KOL names are pinyin guesses.** Every unverified KOL entry is excluded
  from matching (`Tagger.__init__`); `cli status` lists them. Set
  `verified: true` in the dictionary once the operator confirms them.

## Replacing the reconstructions

- **Dictionary:** overwrite `data/domain_map_liver_v3.md`. Keep the four
  `## lines` / `## companies` / `## kol` / `## weights` sections with fenced
  `json` blocks; `domain_map.load` validates that all 16 lines exist and that
  main/aux tiers agree with `config.MAIN_LINES`.
- **Report layout:** `report.daily_markdown` / `report.weekly_markdown`. The
  field contract from section 2 is rendered per item; edit the templates to match
  `liver_daily_2026-09-14.md` once that file is to hand.
- **Tagger vocabulary:** `tagger.STUDY_PATTERNS` for layer 2; layers 1 and 3 read
  the dictionary.

## Output contract

`Item.to_json()` emits exactly, in order:
`src, title, url, date, meta, lines, study, P, why, score, evidence`, where
`evidence` is `{signals: [...], quotes: [{text, url?, locator?, lang?, translation?}]}`.
`why` is a factual join of signal descriptions — no forecast, no evaluation.
The daily run writes both `out/liver_daily_<date>.md` and `.json`.

## Deployment

`scripts/run_daily.sh` runs the daily collection and adds the weekly digest on
Fridays; `scripts/crontab.example` has the schedule (06:30 Beijing, Mon–Fri, plus
a weekly re-verification of the registry). State lives in SQLite at
`data/state.sqlite3`: NCT status, published items, page hashes and the weekly
pool.

## Tests

```bash
python -m pytest        # 82 tests, no network
```

Offline throughout: HTTP is served by a fake fetcher, and the significance step
is exercised against a stub client that returns one true and one invented quote,
asserting the invented one is dropped.
