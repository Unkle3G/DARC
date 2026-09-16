"""T6 -- conference windows.

Inside a congress window (late-breaker release through the end of the meeting,
plus a margin) the engine collects more often and stamps items with a
``conference`` field.

An entry whose dates have not been confirmed is **inert**: it never opens a
window and never tags an item.  The handover asks for the 2026 AASLD dates to
be looked up rather than recalled, and the same rule is applied to every entry
so an unverified date can never quietly change collection behaviour.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .config import CONFERENCES


@dataclass
class Conference:
    id: str
    name: str
    society: str = ""
    start: str | None = None
    end: str | None = None
    late_breaker_release: str | None = None
    verified: bool = False
    source_url: str | None = None
    note: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.verified and self.start and self.end)

    def window(self, days_before: int, days_after: int) -> tuple[date, date] | None:
        if not self.usable:
            return None
        opening = self.late_breaker_release or self.start
        try:
            first = date.fromisoformat(opening) - timedelta(days=days_before)
            last = date.fromisoformat(self.end) + timedelta(days=days_after)
        except (TypeError, ValueError):
            return None
        return first, last


@dataclass
class Calendar:
    conferences: list[Conference]
    boost_days_before: int = 2
    boost_days_after: int = 2
    note: str = ""

    @classmethod
    def load(cls, path: Path | str | None = None) -> "Calendar":
        path = Path(path or CONFERENCES)
        if not path.exists():
            return cls(conferences=[])
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        conferences = [
            Conference(**{k: v for k, v in entry.items()
                          if k in Conference.__dataclass_fields__})
            for entry in raw.get("conferences", [])
        ]
        return cls(conferences=conferences,
                   boost_days_before=int(raw.get("boost_days_before", 2)),
                   boost_days_after=int(raw.get("boost_days_after", 2)),
                   note=raw.get("note", ""))

    def active(self, today: str) -> Conference | None:
        try:
            day = date.fromisoformat(today)
        except ValueError:
            return None
        for conference in self.conferences:
            window = conference.window(self.boost_days_before, self.boost_days_after)
            if window and window[0] <= day <= window[1]:
                return conference
        return None

    def upcoming(self, today: str, limit: int = 4) -> list[tuple[Conference, int]]:
        """Verified conferences still ahead, with days remaining.

        A meeting already under way counts as upcoming until it ends; an
        unverified entry never appears, for the same reason it opens no window.
        """
        try:
            day = date.fromisoformat(today)
        except ValueError:
            return []
        out: list[tuple[Conference, int]] = []
        for conference in self.conferences:
            if not conference.usable:
                continue
            try:
                start = date.fromisoformat(conference.start)
                end = date.fromisoformat(conference.end)
            except (TypeError, ValueError):
                continue
            if end < day:
                continue
            out.append((conference, (start - day).days))
        out.sort(key=lambda pair: pair[1])
        return out[:limit]

    @property
    def unverified(self) -> list[Conference]:
        return [c for c in self.conferences if not c.usable]

    def warnings(self) -> list[str]:
        if not self.unverified:
            return []
        names = ", ".join(f"{c.id}" for c in self.unverified)
        return [
            f"conference calendar: {names} have unconfirmed dates, so their windows "
            f"are inert. Fill data/conferences.json and set verified=true."
        ]
