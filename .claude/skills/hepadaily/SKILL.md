---
name: hepadaily
description: Run the HepaDaily liver-intelligence daily and produce the 公众号 article, with the Chinese renderings supplied by this session. Use for "跑日报", "出今天的 HepaDaily", "翻译日报", or /hepadaily.
---

# HepaDaily daily run

The engine (`liver-intel/`) collects, grades and renders on its own. What it
cannot do without an API credential is translate, so the run hands **this
session** a worksheet of every non-Chinese title, quote and field value that
shipped, and you fill it in. No external model, no key.

## Steps

1. Environment. `LIVER_INTEL_CONTACT` must be set (EDGAR refuses to run
   without it). Do not set or ask for an Anthropic key; the renderings are
   yours to write.

2. Run the daily:

       cd liver-intel && python3 -m liver_intel.cli daily --wechat

   Add `--issue <n>` when the operator names an issue number (`--issue 003` ->
   `HepaDaily｜第003期｜<date>` in the masthead). It is remembered in the day's
   `_run.json`, so `judge` and `render` keep it.

   The worksheet also holds `summary.zh`: a Chinese paragraph of at most 200
   characters that leads the article, before the entries. Write it from the
   `summary.items` digest in the same worksheet -- restate what shipped and
   where it came from, and nothing else. Every number in it must already be in
   the day's material; the engine drops the whole paragraph otherwise and says
   why. Leave it empty and no paragraph prints.

   To reissue an already-published day under a rule fixed since, add `--retag`
   to `judge` (`judge --date <date> --issue <n> --retag`): it re-reads the
   stored text with the current tagger instead of re-collecting, so the issue
   keeps its own material and gains nothing new. It only *adds* tags -- undoing
   a false positive still needs the day collected again.

   If a scheduled 03:00 run already collected today (out/liver_daily_<date>.json
   exists and is not empty), do **not** run `daily` again — it would find
   nothing new and the engine refuses to overwrite. Go straight to step 3 with
   the worksheet that run left behind, then `render`.

   The run refuses to work on a weekend or a Chinese public holiday and prints
   the reason; that is the schedule, not a failure. `--ignore-calendar` forces
   it when the operator asks.

   Read the printed notes. `MODEL STEP DID NOT RUN` is expected on this path
   and means grading was rules-only; say so to the operator. The last line
   names the worksheet, `out/liver_daily_<date>_renderings.json`.

3. Fill the worksheet. Open it, and for every entry write the Chinese
   rendering into `zh`. The file's `rules` are binding:
   - literal and complete; nothing added, nothing explained, no glosses;
   - drug names, codes, compound names, trial names, company names, targets,
     registry ids and journal names stay exactly as written -- no Chinese
     INN substitution (cemiplimab stays cemiplimab), and notation stays
     verbatim (`+/-` stays `+/-`, never `±`); trial phases keep the source's
     wording (`Phase 3` stays `Phase 3`, never `III期`; the worksheet does
     not even offer the registry's `phases` field);
   - the original always comes first and the rendering after it; the
     renderer enforces this, do not work around it;
   - when a reader needs to know what a product or trial *is*, do not write
     a note: add an entry to that document's `supplementary` list -- `text`
     a sentence that exists verbatim in its `source_text`, `zh` its
     rendering. `render` drops anything not found verbatim;
   - no evaluation, no forecast, no 利好/利空/有望/重磅;
   - unsure → leave `zh` empty; the original then stands alone;
   - never edit `text` or `id`.
   Write the file back as JSON (keep `ensure_ascii=False`-style UTF-8).

4. Re-render:

       python3 -m liver_intel.cli render --date <date>

   It reports `N accepted, M rejected` and names every rejected slot with
   the reason (not Chinese, identical to the source, evaluative wording, or
   the source text was changed). Fix and re-run `render` until nothing is
   rejected that should not be.

5. Hand over `out/liver_daily_<date>_wechat.html` — **this is the one the
   operator publishes from**. It is a complete page: charset declared, every
   style inline (the WeChat editor strips stylesheets), so it opens in a
   browser, and select-all-copy lands in the WeChat editor with its formatting
   intact. Nothing else has to be installed or configured.

   Also hand over `out/liver_daily_<date>_mdnice.md` (the same article as plain
   Markdown, kept as a fallback: paste into MDNice, turn on 「微信外链转脚注」 so
   the source links survive, and put `liver-intel/assets/mdnice-theme.css` in
   MDNice's 「自定义」 CSS box) and `out/liver_daily_<date>.md` (internal).
   Point out anything in 运行提示 the operator has to act on. Do not publish the
   article yourself.

On a Friday, also run `python3 -m liver_intel.cli weekly`.

## What you must not do

- Do not summarise, rank or comment on items in the article; the renderer
  adds counts and provenance only, and the same applies to you.
- Do not "improve" a quote or a title. A rendering is a rendering.
- Do not translate Chinese sources into anything.
