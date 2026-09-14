"""Feed verification.

A candidate becomes usable only after this module has fetched it and confirmed
that it parses and carries a date field -- the acceptance bar the handover sets
for T1 ("可访问、有日期字段").
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .feeds import Feed, Registry
from .http import Blocked, Fetcher
from .models import as_iso_date

log = logging.getLogger(__name__)

_DATE_KEYS = ("published", "updated", "pubDate", "date", "lastBuildDate",
              "dateTime", "created", "releaseDate", "filingDate", "lastmod")


def _brief(exc: Exception, limit: int = 200) -> str:
    text = str(exc).replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "..."


@dataclass
class VerifyResult:
    feed: Feed
    ok: bool
    reason: str = ""


def _probe_xml(text: str) -> tuple[int, bool]:
    """Item count and whether a date field is present, for RSS/Atom/sitemap."""
    try:
        import feedparser

        parsed = feedparser.parse(text)
        entries = parsed.entries or []
        if entries:
            has_date = any(
                getattr(entry, "published_parsed", None)
                or getattr(entry, "updated_parsed", None)
                or getattr(entry, "published", None)
                or getattr(entry, "updated", None)
                for entry in entries
            )
            return len(entries), bool(has_date)
    except ImportError:
        log.debug("feedparser missing, falling back to regex probing")
    except Exception as exc:
        log.debug("feedparser failed: %s", exc)

    items = len(re.findall(r"<(?:item|entry|url)\b", text, re.I))
    has_date = bool(re.search(r"<(?:pubDate|published|updated|lastmod|dc:date)\b", text, re.I))
    return items, has_date


def _probe_json(text: str) -> tuple[int, bool]:
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError:
        return 0, False

    def walk(node: Any, depth: int = 0) -> bool:
        if depth > 4:
            return False
        if isinstance(node, dict):
            for key, value in node.items():
                if key in _DATE_KEYS and as_iso_date(value):
                    return True
                if walk(value, depth + 1):
                    return True
        elif isinstance(node, list):
            return any(walk(child, depth + 1) for child in node[:20])
        return False

    if isinstance(payload, list):
        return len(payload), walk(payload)
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list) and value:
                return len(value), walk(payload)
        return (1 if payload else 0), walk(payload)
    return 0, False


def verify_feed(feed: Feed, fetcher: Fetcher) -> VerifyResult:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    feed.last_checked = now
    try:
        response = fetcher.get(feed.url, allow_304=False)
    except Blocked as exc:
        feed.status = "blocked"
        feed.note = (f"refused: {exc}. Not retried -- decide manually whether to "
                     "keep watching this source.")
        return VerifyResult(feed, False, str(exc))
    except Exception as exc:
        # A transport failure (DNS, proxy, timeout) says nothing about the
        # endpoint -- only that we could not reach it from here. Recording it as
        # dead would read as "this URL is wrong", a different claim entirely.
        feed.status = "unreachable"
        feed.note = f"could not be reached from this host: {_brief(exc)}"
        return VerifyResult(feed, False, str(exc))

    feed.http_status = response.status
    feed.content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
    if not response.ok:
        feed.status = "dead"
        feed.note = f"HTTP {response.status}"
        return VerifyResult(feed, False, feed.note)

    body = response.text
    if feed.kind in ("json", "api") or "json" in (feed.content_type or ""):
        count, has_date = _probe_json(body)
    else:
        count, has_date = _probe_xml(body)

    feed.item_count = count
    feed.has_date = has_date

    if count <= 0:
        feed.status = "dead"
        feed.note = "reachable but produced no items"
        return VerifyResult(feed, False, feed.note)
    if not has_date:
        feed.status = "dead"
        feed.note = "reachable but carries no usable date field"
        return VerifyResult(feed, False, feed.note)

    feed.status = "verified"
    feed.note = f"verified {now}: {count} items, date field present"
    return VerifyResult(feed, True)


def verify_registry(registry: Registry, fetcher: Fetcher, only: str | None = None,
                    include_verified: bool = False) -> list[VerifyResult]:
    results = []
    for feed in registry.entries:
        if feed.status == "discover_root":
            continue
        if only and feed.source != only and feed.task != only and feed.id != only:
            continue
        if feed.status == "verified" and not include_verified:
            continue
        results.append(verify_feed(feed, fetcher))
    return results
