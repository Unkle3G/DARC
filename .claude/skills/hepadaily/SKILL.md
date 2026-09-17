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

5. Hand over `out/liver_daily_<date>_wechat.html` (paste into the WeChat
   editor) and `out/liver_daily_<date>.md` (internal). Point out anything in
   运行提示 the operator has to act on. Do not publish the article yourself.

On a Friday, also run `python3 -m liver_intel.cli weekly`.

## What you must not do

- Do not summarise, rank or comment on items in the article; the renderer
  adds counts and provenance only, and the same applies to you.
- Do not "improve" a quote or a title. A rendering is a rendering.
- Do not translate Chinese sources into anything.
