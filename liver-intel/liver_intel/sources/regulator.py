"""T4 -- regulatory announcements (FDA, EMA, NMPA/CDE).

FDA         openFDA ``drug/drugsfda`` for approval records, plus whatever press
            or calendar endpoints the registry has verified.
EMA         verified listing pages (CHMP post-meeting highlights are monthly).
NMPA / CDE  list-page change detection once a day.  These sites push back hard
            on scraping, so on a refusal the adapter raises a manual-check note
            instead of retrying -- exactly what the handover asks for.
"""
from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote, urlencode

from ..models import Item, as_iso_date
from ..textutil import clip, html_to_text
from . import listwatch
from .base import BaseSource, Context
from .newswire import parse_feed

log = logging.getLogger(__name__)

OPENFDA_ID = "openfda.drugsfda"


class FdaSource(BaseSource):
    """openFDA approvals + verified FDA feeds."""

    id = "fda"
    task = "T4"
    src_kind = "regulator"
    requires_feeds = False

    def __init__(self, lookback_days: int = 120):
        self.lookback_days = lookback_days

    def collect(self, ctx: Context) -> list[Item]:
        items: list[Item] = []
        items.extend(self._openfda(ctx))
        items.extend(self._feeds(ctx))
        return self.emit(ctx, items)

    # -- openFDA ----------------------------------------------------------
    def _openfda(self, ctx: Context) -> list[Item]:
        feed = ctx.registry.by_id(OPENFDA_ID)
        if feed is None or not (feed.is_usable or ctx.settings.allow_unverified):
            ctx.note(f"[{self.id}] {OPENFDA_ID} is not verified -- approvals skipped.")
            return []

        out: list[Item] = []
        sponsors = [c for c in ctx.domain_map.companies if c.market in ("us", "eu", "global")]
        for company in sponsors:
            latin = (company.aliases or [company.name])[0]
            query = f'sponsor_name:"{latin}"'
            url = f"{feed.url}?{urlencode({'search': query, 'limit': '20'})}"
            try:
                response = ctx.fetcher.get(url, allow_304=False)
            except Exception as exc:
                ctx.note(f"[{self.id}] openFDA query failed for {latin}: {exc}")
                continue
            if response.status == 404:
                continue          # openFDA answers 404 for an empty result set
            if not response.ok:
                ctx.note(f"[{self.id}] openFDA HTTP {response.status} for {latin}")
                continue
            try:
                payload = response.json()
            except json.JSONDecodeError:
                continue
            for record in payload.get("results", []) or []:
                out.extend(self._from_drugsfda(ctx, company.name, company.tier, record))
        return out

    def _from_drugsfda(self, ctx: Context, company: str, tier: int,
                       record: dict[str, Any]) -> list[Item]:
        app_no = record.get("application_number", "")
        brand = ""
        for product in record.get("products", []) or []:
            brand = product.get("brand_name") or brand
        out: list[Item] = []
        for submission in record.get("submissions", []) or []:
            status_date = as_iso_date(submission.get("submission_status_date"))
            if not status_date:
                continue
            if ctx.since and status_date < ctx.since:
                continue
            status = submission.get("submission_status", "")
            sub_type = submission.get("submission_type", "")
            url = (f"https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm"
                   f"?event=overview.process&ApplNo={quote(app_no.replace('NDA', '').replace('BLA', ''))}")
            title = f"{company} {app_no} {sub_type} {status}".strip()
            if brand:
                title = f"{company} {brand} ({app_no}): {sub_type} {status}"
            item = Item(
                src=self.id, title=title, url=url, date=status_date,
                meta={
                    "src_kind": self.src_kind,
                    "application_number": app_no,
                    "brand_name": brand,
                    "submission_type": sub_type,
                    "submission_status": status,
                    "companies": [company],
                    "company_tier": tier,
                    "regulator": "FDA",
                    "body": (f"{title}. Application {app_no}, submission {sub_type}, "
                             f"status {status} dated {status_date}."),
                },
            )
            if status.upper() == "AP":
                item.study.append("APPROVAL")
            if ctx.store.is_seen(item.key):
                continue
            out.append(item)
        return out

    # -- verified FDA feeds / calendars -----------------------------------
    def _feeds(self, ctx: Context) -> list[Item]:
        out: list[Item] = []
        for feed in ctx.feeds(self.id):
            if feed.kind in ("rss", "atom"):
                out.extend(_items_from_feed(ctx, self, feed))
            elif feed.kind == "html":
                out.extend(_items_from_listwatch(ctx, self, feed, regulator="FDA"))
        return out


class EmaSource(BaseSource):
    id = "ema"
    task = "T4"
    src_kind = "regulator"

    def collect(self, ctx: Context) -> list[Item]:
        out: list[Item] = []
        for feed in ctx.feeds(self.id):
            if feed.kind in ("rss", "atom"):
                out.extend(_items_from_feed(ctx, self, feed))
            else:
                out.extend(_items_from_listwatch(ctx, self, feed, regulator="EMA"))
        return self.emit(ctx, out)


class CnRegulatorSource(BaseSource):
    """NMPA / CDE list pages: once a day, change detection only.

    No item is invented from a hash: a change produces one item that says the
    list changed and links to it, and the operator (or a later adapter) reads
    the page.  That keeps the 'no inference' rule intact when the page cannot
    be parsed into records.
    """

    id = "cn_regulator"
    task = "T4"
    src_kind = "regulator"

    def collect(self, ctx: Context) -> list[Item]:
        out: list[Item] = []
        for feed in ctx.feeds(self.id):
            result = listwatch.check(ctx.store, ctx.fetcher, feed.id, feed.url)
            if result.needs_manual_check:
                ctx.note(
                    f"[{self.id}] {feed.url} could not be read "
                    f"({result.reason or 'repeated failures'}). Anti-scraping is expected "
                    f"here -- check this list by hand today rather than retrying.")
                continue
            if not result.changed:
                continue
            title = feed.note or f"{feed.id} list page changed"
            item = Item(
                src=self.id,
                title=title,
                url=feed.url,
                date=ctx.today,
                meta={
                    "src_kind": self.src_kind,
                    "regulator": "NMPA/CDE",
                    "change_detected": True,
                    "region": "cn",
                    "body": clip(html_to_text(result.body), 20_000),
                    "needs_human_read": True,
                },
            )
            item.study.append("SUBMISSION")
            out.append(item)
        return self.emit(ctx, out)


# --- shared helpers -------------------------------------------------------
def _items_from_feed(ctx: Context, source: BaseSource, feed: Any) -> list[Item]:
    try:
        response = ctx.fetcher.get(feed.url, allow_304=False)
    except Exception as exc:
        ctx.note(f"[{source.id}] {feed.id} unreadable: {exc}")
        return []
    if not response.ok:
        ctx.note(f"[{source.id}] {feed.id} HTTP {response.status}")
        return []
    out: list[Item] = []
    for entry in parse_feed(response.text, feed.url):
        if not entry["url"]:
            continue
        date = entry["date"] or ctx.today
        if ctx.since and date < ctx.since:
            continue
        item = Item(
            src=source.id, title=entry["title"], url=entry["url"], date=date,
            meta={"src_kind": source.src_kind, "feed_id": feed.id,
                  "summary": clip(entry["summary"], 1200),
                  "body": clip(entry["summary"], 1200)},
        )
        if not ctx.store.is_seen(item.key):
            out.append(item)
    return out


def _items_from_listwatch(ctx: Context, source: BaseSource, feed: Any,
                          regulator: str = "") -> list[Item]:
    result = listwatch.check(ctx.store, ctx.fetcher, feed.id, feed.url)
    if result.needs_manual_check:
        ctx.note(f"[{source.id}] {feed.url} needs a manual check: "
                 f"{result.reason or 'repeated failures'}")
        return []
    if not result.changed:
        return []
    return [Item(
        src=source.id,
        title=feed.note or f"{feed.id} page changed",
        url=feed.url,
        date=ctx.today,
        meta={"src_kind": source.src_kind, "regulator": regulator,
              "change_detected": True, "needs_human_read": True,
              "body": clip(html_to_text(result.body), 20_000)},
    )]
