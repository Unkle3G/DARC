"""Hash-based change detection for list pages.

Used where there is no feed and no API: the Chinese regulator listings (T4),
company newsrooms (T7) and calendar pages (T6).  The page body is normalised
(timestamps, nonces and session ids stripped) before hashing so a cosmetic
re-render does not look like news.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

from ..http import Blocked, Fetcher
from ..store import Store

log = logging.getLogger(__name__)

_VOLATILE = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?\b"),   # timestamps
    re.compile(r"(?i)\b(?:csrf|nonce|token|sessionid|jsessionid)=[\w\-]+"),
    re.compile(r"(?i)<!--.*?-->", re.S),
    re.compile(r"(?i)\?v=\d+"),
    re.compile(r"(?i)\b\d{10,13}\b"),                                # epoch stamps
)


def normalise(body: str) -> str:
    text = body or ""
    for pattern in _VOLATILE:
        text = pattern.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def content_hash(body: str) -> str:
    return hashlib.sha256(normalise(body).encode("utf-8", "replace")).hexdigest()


@dataclass
class WatchResult:
    key: str
    url: str
    changed: bool
    body: str = ""
    blocked: bool = False
    failures: int = 0
    reason: str = ""

    @property
    def needs_manual_check(self) -> bool:
        """After repeated refusals we stop hammering and ask a human to look."""
        return self.blocked or self.failures >= 2


def check(store: Store, fetcher: Fetcher, key: str, url: str,
          use_conditional: bool = True) -> WatchResult:
    previous = store.get_page(key)
    etag = previous["etag"] if previous and use_conditional else None
    last_modified = previous["last_modified"] if previous and use_conditional else None

    try:
        response = fetcher.get(url, etag=etag, last_modified=last_modified)
    except Blocked as exc:
        failures = store.record_page_failure(key, url, f"blocked: {exc}")
        return WatchResult(key, url, changed=False, blocked=True, failures=failures,
                           reason=str(exc))
    except Exception as exc:
        failures = store.record_page_failure(key, url, f"error: {exc}")
        return WatchResult(key, url, changed=False, failures=failures, reason=str(exc))

    if response.status == 304:
        store.put_page(key, url, previous["body_hash"] if previous else None,
                       etag=etag, last_modified=last_modified, changed=False,
                       note="304 not modified")
        return WatchResult(key, url, changed=False, reason="not modified")

    if not response.ok:
        failures = store.record_page_failure(key, url, f"HTTP {response.status}")
        return WatchResult(key, url, changed=False, failures=failures,
                           reason=f"HTTP {response.status}")

    digest = content_hash(response.text)
    known = previous["body_hash"] if previous else None
    changed = known is not None and digest != known
    store.put_page(key, url, digest,
                   etag=response.headers.get("ETag"),
                   last_modified=response.headers.get("Last-Modified"),
                   changed=changed,
                   note="first sighting" if known is None else
                        ("changed" if changed else "unchanged"))
    return WatchResult(key, url, changed=changed, body=response.text,
                       reason="first sighting" if known is None else "")
