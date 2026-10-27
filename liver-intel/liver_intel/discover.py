"""Feed discovery.

Finds candidate feeds from a site instead of guessing their paths: RSS/Atom
autodiscovery links in the page head, anchors that look like feeds, and
``/sitemap.xml``.  Everything it returns is a *candidate* -- status
``unverified`` -- and has to survive :mod:`liver_intel.verify` before a
collector will touch it.
"""
from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from .feeds import Feed
from .http import Blocked, Fetcher

log = logging.getLogger(__name__)

FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/feed+json",
              "application/json")
_FEEDISH = re.compile(r"(?:^|/)(?:rss|feed|feeds|atom)(?:[./?]|$)|\.(?:rss|atom)$", re.I)

class _LinkScraper(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.feed_links: list[tuple[str, str, str]] = []   # (href, type, title)
        self.anchors: list[tuple[str, str]] = []           # (href, text)
        self._current_anchor: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k.lower(): (v or "") for k, v in attrs}
        if tag == "link":
            rel = values.get("rel", "").lower()
            mime = values.get("type", "").lower()
            href = values.get("href", "")
            if href and ("alternate" in rel or "feed" in rel) and mime in FEED_TYPES:
                self.feed_links.append((href, mime, values.get("title", "")))
        elif tag == "a":
            href = values.get("href", "")
            if href:
                self._current_anchor = href
                self.anchors.append((href, ""))

    def handle_data(self, data: str) -> None:
        if self._current_anchor and self.anchors:
            href, text = self.anchors[-1]
            self.anchors[-1] = (href, (text + data).strip()[:120])

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._current_anchor = None


def scrape_page(html: str, base_url: str) -> list[tuple[str, str, str]]:
    """Return (url, kind, label) candidates found in one page."""
    parser = _LinkScraper()
    try:
        parser.feed(html)
    except Exception as exc:  # malformed markup should not kill a run
        log.debug("html parse issue on %s: %s", base_url, exc)

    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []

    for href, mime, title in parser.feed_links:
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        kind = "json" if "json" in mime else ("atom" if "atom" in mime else "rss")
        out.append((url, kind, title or "autodiscovery"))

    for href, text in parser.anchors:
        if not _FEEDISH.search(href):
            continue
        url = urljoin(base_url, href)
        if url in seen or urlparse(url).scheme not in ("http", "https"):
            continue
        seen.add(url)
        out.append((url, "rss", text or "anchor"))
    return out


#: Feed paths embedded in inline JSON or JS rather than in markup. PR Newswire
#: renders its feed directory client-side, so every address sits in the page as
#: an escaped JSON string ("\\/rss\\/health\\u002Dlatest\\u002Dnews...") and a scrape
#: of anchors and <link> tags comes back empty from a page listing dozens of feeds.
_EMBEDDED_FEED = re.compile(r"(?:https?://[\w.\-]+)?/[\w\-/.]*\.(?:rss|atom)\b", re.I)

_JSON_ESCAPES = (("\\/", "/"), ("\\u002D", "-"), ("\\u002d", "-"),
                 ("\\u0026", "&"), ("\\u003D", "="), ("\\u003d", "="))


def unescape_embedded(text: str) -> str:
    """Undo the JSON string escaping a page applies to embedded URLs."""
    for escape, char in _JSON_ESCAPES:
        text = text.replace(escape, char)
    return text


def embedded_feeds(html: str, base_url: str) -> list[str]:
    """Feed URLs written into the page's own JSON or JS payloads."""
    out: list[str] = []
    seen: set[str] = set()
    for match in _EMBEDDED_FEED.finditer(unescape_embedded(html or "")):
        url = urljoin(base_url, match.group(0))
        if url in seen or urlparse(url).scheme not in ("http", "https"):
            continue
        seen.add(url)
        out.append(url)
    return out


#: A page whose job is to list feeds. Worth following one level: the feed
#: directory is usually a click away from the homepage, not on it.
_FEED_INDEX = re.compile(r"(?:^|/)(?:rss|feeds?)/?$", re.I)


def discover(root: Feed, fetcher: Fetcher, include_sitemap: bool = True,
             follow_index: bool = True) -> list[Feed]:
    """Probe one discovery root and return candidate feeds."""
    candidates: list[Feed] = []
    try:
        response = fetcher.get(root.url, allow_304=False)
    except Blocked as exc:
        log.warning("discovery blocked for %s: %s", root.url, exc)
        root.status = "blocked"
        root.note = f"discovery blocked: {exc}"
        return []
    except Exception as exc:
        log.warning("discovery failed for %s: %s", root.url, exc)
        return []

    host = urlparse(root.url).netloc.lower()
    for index, (url, kind, label) in enumerate(scrape_page(response.text, root.url)):
        candidates.append(Feed(
            id=f"{root.id}.candidate.{index:02d}",
            url=url, task=root.task, source=root.source, kind=kind,
            src_kind=root.src_kind, status="unverified", company=root.company,
            note=f"discovered from {root.url} ({label})",
        ))

    if follow_index:
        # Follow a feed-directory link one level down, then stop: this is feed
        # discovery, not a crawl.
        for url, _, _ in list(scrape_page(response.text, root.url)):
            if not _FEED_INDEX.search(urlparse(url).path):
                continue
            try:
                page = fetcher.get(url, allow_304=False)
            except Exception as exc:
                log.debug("feed index unreadable %s: %s", url, exc)
                continue
            if not page.ok:
                continue
            for index, found in enumerate(embedded_feeds(page.text, url)):
                candidates.append(Feed(
                    id=f"{root.id}.index.{index:02d}", url=found, task=root.task,
                    source=root.source, kind="rss", src_kind=root.src_kind,
                    status="unverified", company=root.company,
                    note=f"listed on {url}"))
            break

    if include_sitemap:
        sitemap = f"https://{host}/sitemap.xml"
        candidates.append(Feed(
            id=f"{root.id}.sitemap", url=sitemap, task=root.task, source=root.source,
            kind="sitemap", src_kind=root.src_kind, status="unverified",
            company=root.company, note=f"sitemap probe for {host}",
        ))
    return candidates
