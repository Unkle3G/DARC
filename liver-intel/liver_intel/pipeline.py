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
from . import llm, people as people_tools, report, report_wechat, select, translate
from .conference import Calendar
from .config import Settings
from .domain_map import DomainMap, load as load_domain_map
from .feeds import Registry
from .http import Fetcher
from .models import Item, Quote, dedup, today_iso
from .sources import Context, build_sources
from .store import Store
from .tagger import Tagger

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    report_date: str
    collected: int = 0
    daily: list[Item] = field(default_factory=list)
    weekly: list[Item] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    report_path: Path | None = None
    json_path: Path | None = None
    wechat_path: Path | None = None

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


def _attach_rule_evidence(item: Item, source_text: str, tagger: Tagger) -> None:
    """Quote the source for signals the deterministic rules fired.

    The significance step supplies evidence for what it finds; rule signals need
    the same backing, or a P0 could ship with nothing to check it against.
    Sentences are copied verbatim, so they pass ``models.validate_quotes``.
    """
    if not item.evidence.signals or not source_text.strip():
        return
    already = {quote.text.strip() for quote in item.evidence.quotes}
    for tag, sentence in tagger.study_evidence(source_text, item.study):
        sentence = sentence.strip()
        if sentence in already:
            continue
        already.add(sentence)
        item.evidence.quotes.append(Quote(text=sentence, locator=f"tag:{tag}"))
        if len(item.evidence.quotes) >= 4:
            break


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
              with_images: bool = False) -> RunResult:
    settings.ensure_dirs()
    today = today or today_iso(settings.report_tz)
    since = since or default_since(today)
    result = RunResult(report_date=today)
    window_start, _ = report.coverage_window(today)

    with Store(settings.db_path) as store:
        ctx = build_context(settings, store, today, since=since)
        ctx.note(f"collection window opens {since}; report covers {window_start} to {today}")
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

            selection = select.select_daily(items, cap=settings.daily_cap)
            for item in selection.daily:
                if item.date < window_start:
                    item.meta["published_before_window"] = True
            translator = translate.build_translator(enabled=use_llm)
            filled, missing = translate.translate_items(selection.daily, translator)
            if filled or missing:
                ctx.note(f"renderings: {filled} produced, {missing} missing"
                         + ("" if filled else " -- originals stand alone"))
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
            body = report.daily_markdown(
                result.daily, today, ctx.domain_map,
                notes=result.notes, weekly_pool_size=len(result.weekly))
            result.report_path = settings.out_dir / f"liver_daily_{today}.md"
            result.report_path.write_text(body, encoding="utf-8")
            result.json_path = settings.out_dir / f"liver_daily_{today}.json"
            result.json_path.write_text(
                json.dumps([item.to_json() for item in result.daily],
                           ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if wechat:
                article = report_wechat.wechat_html(
                    result.daily, today, ctx.domain_map, notes=result.notes,
                    weekly_pool_size=len(result.weekly))
                result.wechat_path = settings.out_dir / f"liver_daily_{today}_wechat.html"
                result.wechat_path.write_text(article, encoding="utf-8")
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
