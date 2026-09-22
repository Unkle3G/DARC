# 肝病情报引擎 liver-intel

Implementation of the v1 handover (2026-09-14). First-party sources only, every
endpoint verified before use, every graded item backed by quoted source text.

```bash
pip install -e ".[dev]"          # add ".[llm]" for the significance step
export LIVER_INTEL_CONTACT="you@example.com"   # EDGAR requires a contact UA

python -m liver_intel.cli preflight              # can we reach the sources at all?
python -m liver_intel.cli status                 # registry / roster / calendar
python -m liver_intel.cli discover --source newswire
python -m liver_intel.cli verify   --source T1   # promotes candidates to verified
python -m liver_intel.cli daily                  # collect, grade, write the report
python -m liver_intel.cli daily --wechat --with-images   # + 公众号 long-form article
python -m liver_intel.cli weekly                 # Friday digest
python3 scripts/demo_offline.py                  # run the pipeline on canned docs
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

## Reader-facing vs internal

The two outputs are deliberately different documents.

| | 公众号 article (`*_wechat.html`) | internal report (`*.md` / `*.json`) |
| --- | --- | --- |
| Grades | never shown — sections read 今日头条 / 前沿速览 / 最新动态 (`config.SECTION_NAMES`) | P0/P1/P2 |
| Line ids, signal names, adapter ids, score | never shown | all present |
| Per-entry labels | searchable keywords (`keywords.py`) | 线路 / 来源 / 研究标签 / 命中信号 |
| Provenance and links | under every entry: publisher · date · clickable original; the general sourcing note at the end | inline per entry |
| Operator notes | never rendered | printed under 运行提示 |
| Brand | `config.BRAND` = HepaDaily | same |

Titles and quotes keep the source language in both. In the article a
non-Chinese title is headed by its Chinese rendering with the original shown
underneath as 原题, and a non-Chinese quote is followed by its rendering; both
are marked 编者译，仅供参考. A Chinese source is shown as written and never
translated. Renderings come from two places: the significance step translates
the quotes it finds, and `translate.py` fills in the rest for the items that
ship — the title and the sentences the deterministic rules quoted. Every
rendering is checked (`assert_not_evaluative`, must be Chinese, must differ from
the source) and dropped rather than published when it fails. When no model ran
there is no rendering, the original stands alone, and the internal report says
`MODEL STEP DID NOT RUN` at the top of 运行提示.

## Renderings without an API key (the default path)

Most machines running the engine have no Anthropic credential and the
operator has a Claude Code session instead. That session is the translator:
the daily run writes `out/liver_daily_<date>_renderings.json` — one slot per
non-Chinese title, quote and field value that shipped, with the rules inside
the file — the session fills `zh`, and

```bash
python -m liver_intel.cli render --date <date>
```

reads it back through the same acceptance checks as the API step (must be
Chinese, must differ from the source, no evaluative wording; the source text
must be untouched) and rewrites the md / json / 公众号 html. The repo skill
`.claude/skills/hepadaily/SKILL.md` gives the session the whole procedure, so
the workflow moves with the repo to any machine. Nothing is translated that
nobody filled in.

## Model credentials (optional)

Section 0 makes the model step part of the method: the significance step
(`llm.py`) and the renderings (`translate.py`) both need an Anthropic credential
— `ANTHROPIC_API_KEY` in the environment, or a profile from `ant auth login`,
plus `pip install anthropic`. Without one the pipeline still runs, on
deterministic rules alone, and every report produced that way carries the
warning above. Do not publish an article from a rules-only run as if it were
the full method. Only the items selected for the day are sent for rendering, so
the cost is bounded by the daily cap.

## Literature (PubMed)

The adapter searches the disease terms over the last three days and archives by
**collection date** — `sortpubdate` can sit in the future for ahead-of-print
records (handover section 3). One `efetch` call supplies both the authors with
affiliations (for the materials library) and the **abstract**, which becomes
`meta.body` (what the tagger reads) and `meta.quotable` (what may be cited as
evidence — the journal published it). A structured abstract keeps its section
labels, so the tagger sees `RESULTS: ...`; the copyright line is dropped.

### The journal roster

PubMed indexes everything, so "published" says nothing on its own. Which
journals a liver brief should surface is the operator's policy, and it lives in
`data/journals_liver.json` beside the disease lines — tier A (the five the
operator named plus their peers) and tier B (field journals one notch down).
Every `fulljournalname` in it was checked against the PubMed API rather than
written from memory.

Matching cuts the catalogue's qualifier — `Hepatology (Baltimore, Md.)`,
`Clinical gastroenterology and hepatology : the official ...` — and then matches
**exactly**. Not as a prefix: a prefix rule looks right until it puts *Nature
structural & molecular biology* in the same tier as *Nature*.

Two signals come out of it:

| | clinical result in the text | no clinical result |
| --- | --- | --- |
| tier A | `JOURNAL_PIVOTAL` (P1) | `JOURNAL_MAJOR` (P2) |
| tier B | `JOURNAL_MAJOR` (P2) | nothing (P3) |
| off the roster | nothing (P3) | nothing (P3) |

"Clinical result" means the text carries one of `CLINICAL_EVIDENCE` in
`grade.py` (topline, interim, an endpoint verdict, a biopsy endpoint, a trial
phase, real-world data, a safety signal).

**The roster decides weight, not collection.** A journal that is not on it is
still collected, still tagged, still in the weekly digest — it just does not
compete for a daily slot. `operator_confirmed` in the file is `false` until the
operator has been through tier B.

## Materials library (backend only)

Every collected item's contributors go into the `contributors` table: authors in
order (position 1 = first author) with affiliation and ORCID for journal records,
and the issuing company, sponsor, regulator or media contact at position 0 for
documents with no byline. PubMed authorship comes from an `efetch` call, because
`esummary` carries names but not affiliations.

```bash
python -m liver_intel.cli authors --summary            # roll-up by contributor
python -m liver_intel.cli authors --first-only --since 2026-09-01
python -m liver_intel.cli authors --affiliation "Capital Medical"
```

Nothing from this table is ever rendered into an article.

## 公众号 output

`daily --wechat` writes the reader-facing article in two forms. **The HTML is
the publishing path** (the operator's choice): it needs no third-party editor
and no theme to install.

`--issue 003` writes 期号 into the masthead (`HepaDaily｜第003期｜2026-09-20`).
`3`, `003` and `第003期` all mean the same issue. The number is kept in the day's
`_run.json`, so a later `judge` or `render` reprints it without being told
again; `--issue` on those two overrides it.

`judge --retag` re-reads the stored source text with the **current** tagger
before grading, for reissuing a day under a rule fixed after it was collected.
Grading already runs on the current rules, so without it a reissue mixes new
grading with the tags the day happened to be collected under -- which is how a
cohort study stayed at P3 after its journal was added to the roster. It is
opt-in, so re-judging an old day otherwise reproduces what was published. The
re-tag can only **add** tags: ctgov writes the registry's phases into `study`,
pubmed writes `PUBLICATION`, regulator writes `APPROVAL`, and none of it is
re-derivable from the text. So a rule that started *matching* is picked up;
a veto that started *blocking* needs the day collected again.

* `out/liver_daily_<date>_wechat.html` — styled inline (the WeChat editor strips
  `<style>` and `<link>`), sized for the ~677px column, `charset` declared so it
  opens correctly from disk. Open it in a browser, select all, copy, paste into
  the WeChat editor;
* `out/liver_daily_<date>_mdnice.md` — a fallback: the same article as **plain
  Markdown for MDNice (墨滴)**, where the theme does the styling. It carries no HTML at all:
  inline styles would survive MDNice and clash with the theme. Keywords are
  code spans (what MDNice's themes render as chips), the calendar is a table,
  and links stay ordinary Markdown links — turn on MDNice's 「微信外链转脚注」 and
  every 查看原文 becomes a numbered footnote, which is what WeChat allows.

Type size lives in one place per path. In the HTML renderer every size is
written `px(n)`, and `FONT_STEP` (env `LIVER_INTEL_FONT_STEP`, default `-2`)
shifts the whole scale at once, with a 9px floor. Markdown carries no sizes at
all, so the MDNice side is the theme's job: `assets/mdnice-theme.css` is the
matching custom theme (same scale, same accent), pasted into MDNice's
「自定义」 CSS box.

The no-commentary rule still applies. The layout gives the material more room —
section headers, figures, pull quotes — but adds no interpretation: the prose is
limited to counts, scope and provenance, and everything substantive is either an
extracted field or text quoted verbatim. Analysis is an editorial pass by a
human, not something the renderer invents.

`--with-images` downloads the figures **the source document itself published**
(`og:image` and content `<img>`; logos, tracking pixels, SVG icons and anything
under 200px are filtered out in `images.py`). Nothing is pulled from a search
engine or a media site. Each figure is captioned with its source URL because
reuse rights are the operator's call, and WeChat will not hot-link a remote
image — the files are saved under `out/images/` to be uploaded to the account's
own library.

`report_wechat.wechat_html(..., watermark=...)` prints a banner above the
headline; `scripts/demo_offline.py` uses it to stamp its output, since the demo
documents are fabricated copy about real companies.

## Output contract

`Item.to_json()` emits exactly, in order:
`src, title, url, date, meta, lines, study, P, why, score, evidence`, where
`evidence` is `{signals: [...], quotes: [{text, url?, locator?, lang?, translation?}]}`.
`why` is a factual join of signal descriptions — no forecast, no evaluation.
The daily run writes both `out/liver_daily_<date>.md` and `.json`.

## Deployment

`scripts/run_daily.sh` runs the daily collection and adds the weekly digest on
Fridays; `scripts/crontab.example` has the schedule (06:30 Beijing, Mon–Fri,
plus a weekly re-verification of the registry).

**Why 06:30.** The US regular session closes at 16:00 ET and biotech releases
land in the hour or two after it, so the run has to clear the close — and the
offset moves with US daylight saving. 06:30 Beijing is 18:30 ET in summer and
17:30 ET in winter: after the close in both, and still 90 minutes before the
08:00 deadline. Earlier times do not survive winter (05:00 Beijing is exactly
16:00 EST — the close itself). Nothing is lost by running early, only delayed:
the collection window opens a day before the coverage window, so an after-close
release missed one morning is picked up the next and labelled 早于本期覆盖窗口.

**Which days it runs.** Monday to Friday, minus Chinese public holidays. That
is not a weekday test — the State Council moves holidays and designates make-up
working days (调休) — so the dates come from `data/holidays_cn.json`, which
records the notice it was taken from. `liver-intel workday` answers the question
on its own (exit 0 / 1) and the runner calls it first; `daily` checks it too, so
a hand-typed run on a holiday says so rather than quietly producing a report
nobody wanted. `--ignore-calendar` overrides. A year missing from the file is
**not** an error: the run falls back to Monday–Friday and prints the fallback
into 运行提示, because losing every day of intelligence to a stale file is worse
than one unnecessary run. A 调休 Saturday does not run unless
`follow_makeup_workdays` is set true.

**Re-running a day.** Everything collected is in `seen_items`, so a second
`daily` on the same date finds nothing new. The engine refuses to overwrite a
non-empty report with that empty result and says so; to re-issue the day's
article use `liver-intel render --date <date>`. State lives in SQLite at
`data/state.sqlite3`: NCT status, published items, page hashes and the weekly
pool.

Two environment variables are required: `LIVER_INTEL_CONTACT` (the address in
the User-Agent — EDGAR requires one, and the adapter skips itself when it is
missing; nothing is baked into the source) and an Anthropic credential (above).

A daily run collects from one day before its coverage window (`pipeline.default_since`)
— a release published late in the US day carries the previous date and lands
after that morning's run. The seen-items table makes the overlap harmless; an
item dated before the coverage window is labelled 早于本期覆盖窗口 in the
internal report. `--since` overrides this, and `backfill` is the historical
sweep. The registry sweep is bounded the same way and ordered newest-first; a
study first seen already terminated is labelled 首次进入状态表, because the
registry does not record when the status changed.

## Tests

```bash
python -m pytest        # 98 tests, no network
```

Offline throughout: HTTP is served by a fake fetcher, and the significance step
is exercised against a stub client that returns one true and one invented quote,
asserting the invented one is dropped.
