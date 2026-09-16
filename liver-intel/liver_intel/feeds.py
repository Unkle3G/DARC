"""Feed registry.

Section 3 of the handover: the previous version shipped RSS URLs written from
memory and every one of them 404'd.  So no endpoint in this engine is trusted
until it has been probed.

The registry is a JSON file of *candidates*.  Each entry carries a ``status``:

``discover_root``  a host to run feed autodiscovery against (not fetched for content)
``unverified``     a candidate URL that has never been probed
``verified``       probed: reachable, parseable, carries a usable date field
``dead``           probed and unusable (404, no date field, no items)
``unreachable``    could not be probed at all (DNS, proxy, timeout) -- says nothing
                   about the endpoint itself, so the next run retries it
``blocked``        probed and refused (403/451) -- an operator decision, not a retry

:func:`usable` is what the collectors call, and it returns ``verified`` entries
only unless the operator explicitly passes ``allow_unverified``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import FEED_REGISTRY

STATUSES = ("discover_root", "unverified", "verified", "dead", "unreachable",
            "blocked")


@dataclass
class Feed:
    id: str
    url: str
    task: str = ""                 # T1..T8, for traceability back to the brief
    source: str = ""               # adapter id that consumes it
    kind: str = "rss"              # rss | atom | json | html | sitemap | api | discover_root
    src_kind: str = "company"      # maps onto Item.meta.src_kind
    status: str = "unverified"
    company: str | None = None
    #: Verification-only request for an endpoint whose base URL means nothing on
    #: its own: an absolute URL, or a query string appended to ``url``. An API
    #: base answers 404 or an error envelope when probed bare, which says
    #: nothing about whether the endpoint works.
    probe: str = ""
    keywords: list[str] = field(default_factory=list)
    last_checked: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    item_count: int | None = None
    has_date: bool | None = None
    note: str = ""

    @property
    def probe_url(self) -> str:
        if not self.probe:
            return self.url
        if self.probe.startswith(("http://", "https://")):
            return self.probe
        return self.url.rstrip("/") + self.probe if self.probe.startswith("/") \
            else self.url + self.probe

    @property
    def is_usable(self) -> bool:
        return self.status == "verified"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Registry:
    entries: list[Feed] = field(default_factory=list)
    path: Path = FEED_REGISTRY

    # -- io ---------------------------------------------------------------
    @classmethod
    def load(cls, path: Path | str | None = None) -> "Registry":
        path = Path(path or FEED_REGISTRY)
        if not path.exists():
            return cls(entries=[], path=path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries = [
            Feed(**{k: v for k, v in item.items() if k in Feed.__dataclass_fields__})
            for item in raw.get("entries", [])
        ]
        return cls(entries=entries, path=path)

    def save(self, path: Path | str | None = None) -> Path:
        path = Path(path or self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "note": ("Every URL here must be probed by `liver-intel verify` before a "
                     "collector will use it. See section 3 of the handover."),
            "entries": [entry.to_json() for entry in self.entries],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        return path

    # -- queries ----------------------------------------------------------
    def by_id(self, feed_id: str) -> Feed | None:
        for entry in self.entries:
            if entry.id == feed_id:
                return entry
        return None

    def usable(self, source: str | None = None, task: str | None = None,
               allow_unverified: bool = False) -> list[Feed]:
        wanted = {"verified"} | ({"unverified"} if allow_unverified else set())
        out = []
        for entry in self.entries:
            if entry.status not in wanted:
                continue
            if source and entry.source != source:
                continue
            if task and entry.task != task:
                continue
            out.append(entry)
        return out

    def discover_roots(self, source: str | None = None) -> list[Feed]:
        return [e for e in self.entries
                if e.status == "discover_root" and (not source or e.source == source)]

    def upsert(self, feed: Feed) -> Feed:
        existing = self.by_id(feed.id)
        if existing is None:
            self.entries.append(feed)
            return feed
        for key, value in feed.to_json().items():
            if value not in (None, "", [], {}) or key in ("status", "note"):
                setattr(existing, key, value)
        return existing

    def counts(self) -> dict[str, int]:
        out = {status: 0 for status in STATUSES}
        for entry in self.entries:
            out[entry.status] = out.get(entry.status, 0) + 1
        return out

    def coverage(self, company_names: Iterable[str]) -> tuple[float, list[str]]:
        """Fraction of the roster with at least one verified feed (T1 acceptance)."""
        covered = {e.company for e in self.entries if e.is_usable and e.company}
        names = list(company_names)
        missing = [name for name in names if name not in covered]
        if not names:
            return 0.0, []
        return (len(names) - len(missing)) / len(names), missing
