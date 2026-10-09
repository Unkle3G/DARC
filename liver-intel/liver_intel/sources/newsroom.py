"""T7 -- company newsroom change detection (lowest priority).

Only for roster companies that T1/T2/T5 cannot reach: private companies and
issuers that do not use a wire.  Sitemap first (cheap and precise), list-page
hash second.
"""
from __future__ import annotations

import logging
import re
from ..models import Item, as_iso_date, canonical_url
from ..textutil import clip, html_to_text
from . import listwatch
from .base import BaseSource, Context

log = logging.getLogger(__name__)

_URL_ENTRY = re.compile(
    r"<url>\s*<loc>(?P<loc>[^<]+)</loc>(?:\s*<lastmod>(?P<lastmod>[^<]+)</lastmod>)?",
    re.I | re.S)


def uncovered_companies(ctx: Context) -> list[str]:
    """Roster names with no verified newswire/EDGAR/exchange coverage."""
    covered = {feed.company for feed in ctx.registry.entries
               if feed.is_usable and feed.company}
    out = []
    for company in ctx.domain_map.companies:
        if company.name in covered:
            continue
        if company.market in ("us", "global") and company.plain_ticker:
            continue          # EDGAR reaches it
        if company.market in ("hk", "cn"):
            continue          # HKEX / CNINFO reach it
        out.append(company.name)
    return out


class NewsroomSource(BaseSource):
    id = "newsroom"
    task = "T7"
    src_kind = "company"

    def collect(self, ctx: Context) -> list[Item]:
        out: list[Item] = []
        for feed in ctx.feeds(self.id):
            if feed.kind == "sitemap":
                out.extend(self._sitemap(ctx, feed))
            else:
                out.extend(self._page(ctx, feed))
        missing = uncovered_companies(ctx)
        if missing:
            ctx.note(f"[{self.id}] no verified endpoint for: {', '.join(sorted(missing))}")
        return self.emit(ctx, out)

    def _sitemap(self, ctx: Context, feed) -> list[Item]:
        try:
            response = ctx.fetcher.get(feed.url, allow_304=False)
        except Exception as exc:
            ctx.note(f"[{self.id}] sitemap unreadable {feed.url}: {exc}")
            return []
        if not response.ok:
            return []
        out: list[Item] = []
        for match in _URL_ENTRY.finditer(response.text):
            loc = match.group("loc").strip()
            lastmod = as_iso_date(match.group("lastmod"))
            if not lastmod:
                continue
            if ctx.since and lastmod < ctx.since:
                continue
            if lastmod < ctx.today and not ctx.since:
                continue
            item = Item(
                src=self.id, title=_title_from_url(loc), url=loc, date=lastmod,
                meta={"src_kind": self.src_kind, "feed_id": feed.id,
                      "companies": [feed.company] if feed.company else [],
                      "discovered_via": "sitemap"},
            )
            if ctx.store.is_seen(item.key):
                continue
            body = self._body(ctx, canonical_url(loc))
            if body:
                item.meta["body"] = clip(body, 20_000)
            out.append(item)
        return out

    def _page(self, ctx: Context, feed) -> list[Item]:
        result = listwatch.check(ctx.store, ctx.fetcher, feed.id, feed.url)
        if result.needs_manual_check:
            ctx.note(f"[{self.id}] {feed.url} needs a manual check: {result.reason}")
            return []
        if not result.changed:
            return []
        return [Item(
            src=self.id,
            title=f"{feed.company or feed.id} newsroom changed",
            url=feed.url, date=ctx.today,
            meta={"src_kind": self.src_kind, "change_detected": True,
                  "companies": [feed.company] if feed.company else [],
                  "needs_human_read": True,
                  "body": clip(html_to_text(result.body), 20_000)},
        )]

    @staticmethod
    def _body(ctx: Context, url: str) -> str:
        try:
            response = ctx.fetcher.get(url, allow_304=False)
        except Exception:
            return ""
        return html_to_text(response.text) if response.ok else ""


def _title_from_url(url: str) -> str:
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"\.(?:html?|aspx|php)$", "", slug)
    return slug.replace("-", " ").replace("_", " ").strip().capitalize() or url
