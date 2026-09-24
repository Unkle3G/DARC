"""Orchestration: collect -> tag -> judge -> grade -> select -> render."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from datetime import date, timedelta

from . import grade as grading
from . import images as image_tools
from . import (llm, people as people_tools, report, report_md, report_wechat,
               select, translate, worksheet)
from .conference import Calendar
from .config import Settings
from .domain_map import DomainMap, load as load_domain_map
from .feeds import Registry
from .http import Fetcher
from .models import Item, Quote, dedup, today_iso
from .sources import Context, build_sources
from .store import Store
from .tagger import Tagger
from .textutil import abstract_findings
from .workdays import Verdict, verdict as workday_verdict

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    report_date: str
    collected: int = 0
    daily: list[Item] = field(default_factory=list)
    weekly: list[Item] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: The masthead issue number, e.g. ``第003期``. Carried in the run file so a
    #: later ``judge``/``render`` keeps the number the day was published under
    #: instead of silently dropping it.
    issue: str = ""
    #: The opening summary, written into the renderings worksheet by the
    #: operator's session. Carried in the run file for the same reason as the
    #: issue number: a later ``render`` must not silently drop it.
    summary: str = ""
    #: Set when the calendar said this is not a working day and nothing ran.
    skipped: Verdict | None = None
    report_path: Path | None = None
    json_path: Path | None = None
    wechat_path: Path | None = None
    markdown_path: Path | None = None
    worksheet_path: Path | None = None
    judgement_path: Path | None = None

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in self.daily:
            out[item.P or "?"] = out.get(item.P or "?", 0) + 1
        return out


def build_context(settings: Settings, store: Store, today: str,
                  since: str | None = None,
                  domain_map: DomainMap | None = None) -> Context:
    calendar = Calendar.load()
    active = calendar.active(today)
    ctx = Context(
        settings=settings,
        fetcher=Fetcher(user_agent=settings.user_agent, rate_limits=settings.rate_limits),
        store=store,
        registry=Registry.load(),
        domain_map=domain_map or load_domain_map(),
        today=today,
        since=since,
        conference_window={"id": active.id, "name": active.name} if active else None,
    )
    for warning in calendar.warnings():
        ctx.note(warning)
    if active:
        ctx.note(f"conference window open: {active.name}")
    return ctx


def collect(ctx: Context, only: list[str] | None = None) -> list[Item]:
    items: list[Item] = []
    for source in build_sources(only):
        found = source.run(ctx)
        log.info("[%s] %d items", source.id, len(found))
        items.extend(found)
    return dedup(items)


def enrich(items: Iterable[Item], ctx: Context, judge: llm.Judge,
           tagger: Tagger | None = None) -> list[Item]:
    """Tag, run the significance step, then grade."""
    tagger = tagger or Tagger(ctx.domain_map)
    out: list[Item] = []
    for item in items:
        tagger.apply(item)
        if ctx.conference_window:
            item.meta["conference"] = ctx.conference_window["name"]
        # Only text the source itself published may be quoted as evidence. An
        # adapter that synthesises a readable body (the registry one does) puts
        # the genuine part in meta["quotable"]; without that the body is the
        # fetched document and is quotable as it stands.
        quotable = "\n".join(filter(None, [
            item.title,
            str(item.meta.get("quotable") or item.meta.get("lead")
                or item.meta.get("body") or ""),
            str(item.meta.get("summary") or ""),
        ]))
        if item.lines:
            # Out-of-scope documents are dropped by the selector whatever the
            # model says; sending them costs half the day's model budget for
            # nothing (77 of 151 collected items matched no line).
            llm.apply(item, quotable, judge)
        grading.apply(item, dm=ctx.domain_map, today=ctx.today,
                      extra_signals=item.evidence.signals)
        _attach_rule_evidence(item, quotable, tagger)
        if not item.meta.get("contributors"):
            # No byline (company release, filing, registry record): record the
            # issuing body so the materials library still has provenance.
            found = people_tools.issuer_contributors(
                item.meta, str(item.meta.get("body") or ""))
            if found:
                item.meta["contributors"] = found
        out.append(item)
    return out


#: How many quotes the deterministic rules may attach to one item.
RULE_QUOTE_CAP = 4


def _attach_rule_evidence(item: Item, source_text: str, tagger: Tagger) -> None:
    """Quote the source for signals the deterministic rules fired.

    The significance step supplies evidence for what it finds; rule signals need
    the same backing, or a P0 could ship with nothing to check it against.
    Sentences are copied verbatim, so they pass ``models.validate_quotes``.

    A paper's findings come first, then the study-tag sentences. The tags are
    vocabulary about what *kind* of study this is -- PHASE2, PRECLINICAL,
    REAL_WORLD -- so the sentence they match is the one saying "this was a
    preclinical model", almost never the one saying what the model showed. A
    measured day had three of five journal articles shipping with no rule quote
    at all and the other two quoting closing boilerplate.
    """
    if not source_text.strip():
        return
    already = {quote.text.strip() for quote in item.evidence.quotes}

    def add(text: str, locator: str) -> bool:
        text = text.strip()
        if not text or text in already:
            return True
        already.add(text)
        item.evidence.quotes.append(Quote(text=text, locator=locator))
        return len(item.evidence.quotes) < RULE_QUOTE_CAP

    # A paper's findings are attached whether or not a rule signal fired. An
    # item with no signal does not compete for a daily slot anyway, but it does
    # reach the weekly digest, and the judgement step can lift it later -- by
    # which time this function has already run, so waiting for a signal would
    # leave exactly the lifted items with nothing to quote.
    if item.meta.get("src_kind") == "journal":
        for label, sentence in abstract_findings(source_text):
            if not add(sentence, f"摘要:{label}"):
                return
    if not item.evidence.signals:
        return
    for tag, sentence in tagger.study_evidence(source_text, item.study):
        if not add(sentence, f"tag:{tag}"):
            return


def fetch_images(items: Iterable[Item], ctx: Context, out_dir: Path) -> int:
    """Download each item's candidate figures so they can be uploaded to WeChat."""
    saved = 0
    for item in items:
        candidates = image_tools.from_meta(item.meta)
        if not candidates:
            continue
        target = out_dir / "images" / item.key[:12]
        got = image_tools.download(candidates, ctx.fetcher, target)
        item.meta["images"] = [c.to_json() for c in got]
        saved += sum(1 for c in got if c.local_path)
    return saved


def default_since(report_date: str) -> str:
    """Where a daily run's collection window opens.

    One day before the report's coverage window: a release published late in
    the US day carries the previous calendar date and lands after that
    morning's run. The seen-items table makes the overlap harmless, and an item
    dated before the coverage window is labelled in the report as a late
    pickup. Without a bound the first run swept whatever each source held --
    weeks of releases and three months of exchange filings -- under a header
    claiming to cover two days.
    """
    start, _ = report.coverage_window(report_date)
    return (date.fromisoformat(start) - timedelta(days=1)).isoformat()


def _collapse(notes: list[str]) -> list[str]:
    """Repeat a note once with a count; 66 identical lines are one fact."""
    counts: dict[str, int] = {}
    for note in notes:
        counts[note] = counts.get(note, 0) + 1
    return [note if n == 1 else f"{note} × {n}" for note, n in counts.items()]


def run_daily(settings: Settings, today: str | None = None, since: str | None = None,
              only: list[str] | None = None, use_llm: bool = True,
              write: bool = True, wechat: bool = False,
              with_images: bool = False, ignore_calendar: bool = False,
              issue: str | None = None) -> RunResult:
    settings.ensure_dirs()
    today = today or today_iso(settings.report_tz)
    if not ignore_calendar:
        # Monday to Friday, minus Chinese public holidays. Checked here rather
        # than only in the shell runner, so a hand-typed run on a holiday says
        # so instead of quietly producing a report nobody wanted.
        decision = workday_verdict(today)
        if not decision.run:
            return RunResult(report_date=today, skipped=decision,
                             notes=[f"未运行：{decision.reason}"])
    else:
        decision = None
    since = since or default_since(today)
    result = RunResult(report_date=today, issue=issue or "")
    window_start, _ = report.coverage_window(today)

    with Store(settings.db_path) as store:
        ctx = build_context(settings, store, today, since=since)
        ctx.note(f"collection window opens {since}; report covers {window_start} to {today}")
        if not ignore_calendar and decision.unverified:
            ctx.note(decision.reason)
        with store.run("daily") as stats:
            items = collect(ctx, only=only)
            result.collected = len(items)
            judge = llm.build_judge(enabled=use_llm)
            if isinstance(judge, llm.NullJudge):
                # Section 0 makes the model step part of the method; a run
                # without it is a different, weaker run and must say so.
                ctx.note("MODEL STEP DID NOT RUN: "
                         f"{llm.credential_status() or 'disabled by --no-llm'}. "
                         "Grading is rules-only and nothing is translated.")
            items = enrich(items, ctx, judge)

            if write and isinstance(judge, llm.NullJudge):
                # No API step ran, so the significance judgement is the
                # session's to make. It sits before selection -- what it finds
                # changes the grade, which changes what ships -- so every
                # candidate is offered, not just what the rules already chose.
                candidates = [item for item in items if item.lines]
                (settings.out_dir / f"liver_daily_{today}_candidates.json").write_text(
                    json.dumps([i.to_json() for i in candidates], ensure_ascii=False,
                               indent=2) + "\n", encoding="utf-8")
                result.judgement_path = (settings.out_dir /
                                         f"liver_daily_{today}_judgement.json")
                count = worksheet.write_judgement(candidates, result.judgement_path, today)
                ctx.note(f"判定工作单：{count} 条待判，填好 signals/quotes 后运行 "
                         f"`liver-intel judge --date {today}`")
            selection = select.select_daily(items, cap=settings.daily_cap)
            for item in selection.daily:
                if item.date < window_start:
                    item.meta["published_before_window"] = True
            translator = translate.build_translator(enabled=use_llm)
            filled, missing = translate.translate_items(selection.daily, translator)
            if filled or missing:
                ctx.note(f"renderings: {filled} produced, {missing} missing"
                         + ("" if filled else " -- originals stand alone"))
            if missing and write:
                # No model of its own: hand the slots to the operator's session.
                result.worksheet_path = settings.out_dir / f"liver_daily_{today}_renderings.json"
                worksheet.write(selection.daily, result.worksheet_path, today)
                ctx.note(f"renderings worksheet: fill `zh` in {result.worksheet_path.name}, "
                         f"then run `liver-intel render --date {today}`")
            for item in selection.weekly:
                store.push_weekly(item)
            published = {item.key for item in selection.daily}
            for item in items:
                # Everything collected is recorded, including what was dropped as
                # out of scope: otherwise the next run fetches those bodies again
                # and the collected count never settles.
                store.mark_seen(item, published_on=today if item.key in published else None)
            # The materials library keeps everything collected, not just what
            # shipped: the point is later retrieval and roll-ups.
            recorded = sum(store.record_contributors(item) for item in items)
            stats["contributors"] = recorded

            result.daily = selection.daily
            result.weekly = selection.weekly
            result.notes = _collapse(
                list(ctx.notes) + [reason for _, reason in selection.dropped])
            stats.update({"collected": result.collected,
                          "daily": len(result.daily),
                          "weekly": len(result.weekly)})

        if with_images and result.daily:
            saved = fetch_images(result.daily, ctx, settings.out_dir)
            result.notes.append(
                f"downloaded {saved} figure(s) for the WeChat article"
                if saved else
                "no figure could be downloaded; the article falls back to remote URLs")

        if write:
            existing = settings.out_dir / f"liver_daily_{today}.json"
            if not result.daily and existing.exists() and \
                    json.loads(existing.read_text(encoding="utf-8")):
                # Everything collected earlier today is already in seen_items,
                # so a second run finds nothing -- and writing that out would
                # replace the morning's report with an empty one. Re-render
                # instead: `liver-intel render --date <date>`.
                result.notes.append(
                    f"已存在 {today} 的报告且本次无新条目，未覆盖；"
                    f"如需重新出稿请用 render --date {today}")
            else:
                _write_outputs(settings, result, ctx.domain_map, wechat=wechat)
    return result


def _write_outputs(settings: Settings, result: RunResult, dm: DomainMap,
                   wechat: bool) -> None:
    today = result.report_date
    body = report.daily_markdown(result.daily, today, dm, notes=result.notes,
                                 weekly_pool_size=len(result.weekly),
                                 issue=result.issue, summary=result.summary)
    result.report_path = settings.out_dir / f"liver_daily_{today}.md"
    result.report_path.write_text(body, encoding="utf-8")
    result.json_path = settings.out_dir / f"liver_daily_{today}.json"
    result.json_path.write_text(
        json.dumps([item.to_json() for item in result.daily],
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # What `render` needs to rebuild the same outputs later.
    (settings.out_dir / f"liver_daily_{today}_run.json").write_text(
        json.dumps({"date": today, "notes": result.notes, "weekly": len(result.weekly),
                    "wechat": wechat, "issue": result.issue,
                    "summary": result.summary},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    if wechat:
        article = report_wechat.wechat_html(result.daily, today, dm, notes=result.notes,
                                            weekly_pool_size=len(result.weekly),
                                            issue=result.issue,
                                            summary=result.summary)
        result.wechat_path = settings.out_dir / f"liver_daily_{today}_wechat.html"
        result.wechat_path.write_text(article, encoding="utf-8")
        # The same article as Markdown, for pasting into MDNice.
        markdown = report_md.wechat_markdown(result.daily, today, dm, notes=result.notes,
                                             weekly_pool_size=len(result.weekly),
                                             issue=result.issue,
                                             summary=result.summary)
        result.markdown_path = settings.out_dir / f"liver_daily_{today}_mdnice.md"
        result.markdown_path.write_text(markdown, encoding="utf-8")


#: Notes a later ``judge`` or ``render`` derives again on every pass. Such a
#: pass has to drop the previous one's or they stack: reissuing 第004期 printed
#: the first pass's "17 处待填" directly above the second's "20 处待填", and a
#: re-render printed the summary it had just rejected beside the one it took.
#: The per-item judgement reasons are matched on " 判定：" because each is
#: prefixed with its own item key.
_DERIVED_NOTES = ("renderings", "判定工作单", "MODEL STEP DID NOT RUN",
                  "判定来自工作单", "译文工作单", "已用当前规则重新打标", "导读")


def _same_selection(settings: Settings, today: str, daily: list[Item]) -> bool:
    """Whether this selection is the one the day's stored report already holds."""
    json_path = settings.out_dir / f"liver_daily_{today}.json"
    if not json_path.exists():
        return False
    try:
        stored = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    # ``key`` is derived from src + canonical url and is not serialised, so the
    # stored items have to be rebuilt to be compared at all.
    return bool(daily) and [Item.from_json(raw).key for raw in stored] == \
        [item.key for item in daily]


def _from_collection(note: str) -> bool:
    """Whether a stored note came from the day's collection, not from a later pass."""
    return not (note.startswith(_DERIVED_NOTES) or " 判定：" in note)


def judge(settings: Settings, today: str, wechat: bool | None = None,
          issue: str | None = None, retag: bool = False) -> RunResult:
    """Apply a filled judgement worksheet: re-grade, re-select, rewrite.

    Nothing is collected again. The candidates are the day's own file, the
    signals come back through the same three guards the API step applies, and
    the day's renderings worksheet is rewritten because selection may have
    changed.

    ``retag`` re-reads the stored source text with the *current* tagger, for
    reissuing a day under a rule fixed after it was collected. Grading already
    runs on the current rules, so without it a reissue mixes new grading with
    the tags the day happened to be collected under. It is opt-in so that
    re-judging an old day reproduces what was published unless a reissue is
    actually meant.

    The re-tag can only **add** tags. ``Tagger.apply`` unions into
    ``item.study``, and it has to: ctgov writes the registry's phases there,
    pubmed writes PUBLICATION, regulator writes APPROVAL and SUBMISSION, and
    clearing the field to re-derive it would throw those away with no way to
    get them back. So a rule that started matching is picked up here; a veto
    that started blocking needs the day collected again.
    """
    candidates_path = settings.out_dir / f"liver_daily_{today}_candidates.json"
    sheet = settings.out_dir / f"liver_daily_{today}_judgement.json"
    if not candidates_path.exists():
        raise FileNotFoundError(f"no candidates for {today}: {candidates_path}")
    if not sheet.exists():
        raise FileNotFoundError(f"no judgement worksheet for {today}: {sheet}")
    items = [Item.from_json(raw)
             for raw in json.loads(candidates_path.read_text(encoding="utf-8"))]
    dm = load_domain_map()
    retag_note = ""
    if retag:
        before = {item.key: set(item.study) for item in items}
        tagger = Tagger(dm)
        for item in items:
            tagger.apply(item)
            # The stored signals are the *previous* run's, rules included, and
            # they were being handed back to the grader as ``extra_signals`` --
            # the slot meant for a model's or the worksheet's findings. So a
            # rule tightened since was outvoted by its own earlier verdict:
            # three NEJM letters kept the JOURNAL_MAJOR that the roster had
            # just stopped awarding them, and --retag looked like it did
            # nothing. Under --retag the rules speak fresh and only the
            # worksheet carries findings.
            item.evidence.signals = []
        changed = sum(1 for item in items if set(item.study) != before[item.key])
        retag_note = (f"已用当前规则重新打标：{len(items)} 条中 {changed} 条 study 标签有变动"
                      f"（study 只增不减；句级标签会重算，所以规则里的否决也会生效）；"
                      f"上一次的信号一律作废，只认当前规则与工作单")
    applied, rejected, reasons = worksheet.apply_judgement(items, sheet)

    for item in items:
        grading.apply(item, dm=dm, today=today, extra_signals=item.evidence.signals)
    selection = select.select_daily(items, cap=settings.daily_cap)

    run_path = settings.out_dir / f"liver_daily_{today}_run.json"
    run_info = json.loads(run_path.read_text(encoding="utf-8")) if run_path.exists() else {}
    notes = [n for n in run_info.get("notes", []) if _from_collection(n)]
    if retag_note:
        notes.append(retag_note)
    notes.append(f"判定来自工作单：{applied} 条信号采纳，{rejected} 条丢弃")
    notes.extend(reasons)
    # The summary describes a particular selection, so it survives only while
    # that selection does. A judge that changes what shipped invalidates the
    # paragraph about it, and carrying it over would publish a description of
    # an issue that no longer exists.
    previous = str(run_info.get("summary") or "")
    kept = _same_selection(settings, today, selection.daily)
    if previous and not kept:
        notes.append("导读已作废：本次判定改变了入选条目，请在译文工作单的 summary 里重写")
    result = RunResult(report_date=today, daily=selection.daily,
                       weekly=selection.weekly, collected=len(items), notes=notes,
                       issue=issue if issue is not None else run_info.get("issue", ""),
                       summary=previous if kept else "")
    _, missing = translate.translate_items(result.daily, translate.NullTranslator())
    if missing:
        result.worksheet_path = settings.out_dir / f"liver_daily_{today}_renderings.json"
        worksheet.write(result.daily, result.worksheet_path, today,
                        summary=result.summary)
        result.notes.append(f"译文工作单已按新选集重写：{missing} 处待填，"
                            f"填好后运行 `liver-intel render --date {today}`")
    _write_outputs(settings, result, dm,
                   wechat=bool(run_info.get("wechat")) if wechat is None else wechat)
    return result


def render(settings: Settings, today: str, wechat: bool | None = None,
           issue: str | None = None) -> RunResult:
    """Re-render a day's outputs after the renderings worksheet was filled in.

    Nothing is collected again: the items are the day's JSON, the renderings
    come from the worksheet through the same acceptance checks as the API
    step, and the reports are rewritten in place.
    """
    json_path = settings.out_dir / f"liver_daily_{today}.json"
    if not json_path.exists():
        raise FileNotFoundError(f"no daily run for {today}: {json_path}")
    items = [Item.from_json(raw) for raw in json.loads(json_path.read_text(encoding="utf-8"))]
    run_path = settings.out_dir / f"liver_daily_{today}_run.json"
    run_info = json.loads(run_path.read_text(encoding="utf-8")) if run_path.exists() else {}
    result = RunResult(report_date=today, daily=items,
                       weekly=[None] * int(run_info.get("weekly") or 0),  # type: ignore[list-item]
                       notes=[n for n in run_info.get("notes", [])
                              if _from_collection(n)],
                       issue=issue if issue is not None else run_info.get("issue", ""),
                       summary=str(run_info.get("summary") or ""))
    sheet = settings.out_dir / f"liver_daily_{today}_renderings.json"
    if sheet.exists():
        accepted, rejected, reasons = worksheet.apply(items, sheet)
        result.notes.append(f"renderings from worksheet: {accepted} accepted, {rejected} rejected")
        result.notes.extend(reasons)
        summary, why = worksheet.apply_summary(items, sheet)
        if summary:
            result.summary = summary
            result.notes.append(f"导读：{translate.summary_length(summary)} 字，已采用")
        elif why:
            result.notes.append(why)
    _, missing = translate.translate_items(items, translate.NullTranslator())
    if missing:
        result.notes.append(f"renderings still missing: {missing} -- originals stand alone")
    _write_outputs(settings, result, load_domain_map(),
                   wechat=bool(run_info.get("wechat")) if wechat is None else wechat)
    return result


def run_weekly(settings: Settings, today: str | None = None,
               limit: int | None = None, write: bool = True) -> RunResult:
    settings.ensure_dirs()
    today = today or today_iso(settings.report_tz)
    result = RunResult(report_date=today)
    with Store(settings.db_path) as store:
        ctx = build_context(settings, store, today)
        pooled = store.drain_weekly(mark_drained_on=today)
        result.weekly = select.rank_weekly(pooled, limit=limit)
        result.notes = list(ctx.notes)
        if write:
            body = report.weekly_markdown(result.weekly, today, ctx.domain_map,
                                          notes=result.notes)
            result.report_path = settings.out_dir / f"liver_weekly_{today}.md"
            result.report_path.write_text(body, encoding="utf-8")
    return result
