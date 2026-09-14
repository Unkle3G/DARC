"""T1 -- company-level newswire feeds (GlobeNewswire / Business Wire / PR Newswire).

The adapter consumes whatever the registry has marked ``verified`` for source
``newswire``: per-organization feeds where the wire offers them, keyword feeds
as the fallback.  Nothing is hard-coded -- see :mod:`liver_intel.discover` and
:mod:`liver_intel.verify` for how entries get there.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from ..models import Item, as_iso_date, canonical_url, is_excluded_host
from ..textutil import clip, html_to_text
from .base import BaseSource, Context

log = logging.getLogger(__name__)

#: Keyword fallback feeds, per the handover.  These are the search terms, not
#: URLs -- the wire-specific URL that carries them is discovered and verified.
FALLBACK_KEYWORDS = (
    "hepatitis B", "MASH", "NASH", "MASLD", "primary biliary cholangitis",
    "hepatocellular carcinoma", "cirrhosis", "hepatitis delta",
)


def parse_feed(text: str, feed_url: str) -> list[dict[str, Any]]:
    """Parse an RSS/Atom body into plain dicts."""
    entries: list[dict[str, Any]] = []
    try:
        import feedparser

        parsed = feedparser.parse(text)
        for entry in parsed.entries:
            entries.append({
                "title": (entry.get("title") or "").strip(),
                "url": (entry.get("link") or "").strip(),
                "date": as_iso_date(entry.get("published") or entry.get("updated")),
                "summary": html_to_text(entry.get("summary") or ""),
                "author": entry.get("author") or "",
            })
        if entries:
            return entries
    except ImportError:
        log.warning("feedparser is not installed; falling back to a regex parse of %s",
                    feed_url)
    except Exception as exc:
        log.warning("feed parse failed for %s: %s", feed_url, exc)

    for block in re.findall(r"<(?:item|entry)\b.*?</(?:item|entry)>", text,
                            re.S | re.I):
        title = _tag(block, "title")
        link = _tag(block, "link") or _attr(block, "link", "href")
        date = _tag(block, "pubDate") or _tag(block, "published") or _tag(block, "updated")
        if not (title and link):
            continue
        entries.append({"title": title, "url": link, "date": as_iso_date(date),
                        "summary": html_to_text(_tag(block, "description") or ""),
                        "author": ""})
    return entries


def _tag(block: str, name: str) -> str:
    match = re.search(rf"<{name}[^>]*>(.*?)</{name}>", block, re.S | re.I)
    if not match:
        return ""
    inner = match.group(1)
    inner = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", inner, flags=re.S)
    return html_to_text(inner).strip()


def _attr(block: str, tag: str, attr: str) -> str:
    match = re.search(rf"<{tag}[^>]*\b{attr}=[\"']([^\"']+)[\"']", block, re.I)
    return match.group(1) if match else ""


class NewswireSource(BaseSource):
    id = "newswire"
    task = "T1"
    src_kind = "company"

    def __init__(self, fetch_bodies: bool = True, max_bodies: int = 40):
        self.fetch_bodies = fetch_bodies
        self.max_bodies = max_bodies

    def collect(self, ctx: Context) -> list[Item]:
        items: list[Item] = []
        bodies_fetched = 0
        for feed in ctx.feeds(self.id):
            try:
                response = ctx.fetcher.get(feed.url, allow_304=False)
            except Exception as exc:
                ctx.note(f"[{self.id}] {feed.id} unreadable: {exc}")
                continue
            if not response.ok:
                ctx.note(f"[{self.id}] {feed.id} returned HTTP {response.status}")
                continue

            for entry in parse_feed(response.text, feed.url):
                url = entry["url"]
                if not url or is_excluded_host(url):
                    continue
                date = entry["date"] or ctx.today
                if ctx.since and date < ctx.since:
                    continue
                key_url = canonical_url(url)
                item = Item(
                    src=self.id,
                    title=entry["title"],
                    url=url,
                    date=date,
                    meta={
                        "src_kind": self.src_kind,
                        "feed_id": feed.id,
                        # The wire is identified from the release URL; a keyword
                        # feed can carry releases from more than one wire.
                        "wire": _wire_name(url) or _wire_name(feed.url),
                        "summary": clip(entry["summary"], 1200),
                    },
                )
                if feed.company:
                    item.meta["feed_company"] = feed.company
                if ctx.store.is_seen(item.key):
                    continue
                if self.fetch_bodies and bodies_fetched < self.max_bodies:
                    body = self._body(ctx, key_url)
                    if body:
                        item.meta["body"] = clip(body, 20_000)
                        bodies_fetched += 1
                items.append(item)
        return self.emit(ctx, items)

    @staticmethod
    def _body(ctx: Context, url: str) -> str:
        try:
            response = ctx.fetcher.get(url, allow_304=False)
        except Exception as exc:
            log.debug("body fetch failed for %s: %s", url, exc)
            return ""
        if not response.ok:
            return ""
        return html_to_text(response.text)


def _wire_name(url: str) -> str:
    for host, name in (("globenewswire", "GlobeNewswire"),
                       ("businesswire", "Business Wire"),
                       ("prnewswire", "PR Newswire")):
        if host in url:
            return name
    return ""
