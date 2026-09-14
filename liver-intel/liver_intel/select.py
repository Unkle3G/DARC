"""Daily / weekly selection (task sheet section 0).

Rules, in the order they are applied:

1. **P3 never enters the daily report.**  It goes to the weekly pool.
2. **Auxiliary-only items never enter the daily report**, whatever their
   priority -- the grader has already capped them at P2, and this is the second
   gate.  L13 (AI) carries full weight but is still auxiliary.
3. **Every P0 ships.**  P0 has no cap.
4. **P0 + P1 + P2 is capped at 10.**  P1 and P2 fill whatever the P0 set leaves,
   highest score first.  When P0 alone already meets or exceeds the cap, P1 and
   P2 get no slots and the day's report is P0-only.
5. Everything not selected is pushed to the weekly pool.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import MAIN_LINES
from .models import Item


@dataclass
class Selection:
    daily: list[Item] = field(default_factory=list)
    weekly: list[Item] = field(default_factory=list)
    dropped: list[tuple[Item, str]] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in self.daily:
            out[item.P or "?"] = out.get(item.P or "?", 0) + 1
        return out


def has_main_line(item: Item) -> bool:
    return any(line in MAIN_LINES for line in item.lines)


def select_daily(items: list[Item], cap: int = 10) -> Selection:
    selection = Selection()
    eligible: list[Item] = []

    for item in items:
        if not item.lines:
            selection.weekly.append(item)
            selection.dropped.append((item, "no line matched"))
            continue
        if item.P == "P3":
            selection.weekly.append(item)
            continue
        if not has_main_line(item):
            selection.weekly.append(item)
            selection.dropped.append(
                (item, f"auxiliary-only ({','.join(item.lines)}): weekly digest only"))
            continue
        eligible.append(item)

    ranked = sorted(eligible, key=lambda i: (-i.score, i.date, i.title))
    p0 = [i for i in ranked if i.P == "P0"]
    rest = [i for i in ranked if i.P in ("P1", "P2")]

    # P0 is uncapped; the cap governs the combined set, so P1/P2 take the
    # remainder -- which is nothing once P0 has filled or overrun the cap.
    remaining = max(cap - len(p0), 0)
    selection.daily = p0 + rest[:remaining]
    selection.weekly.extend(rest[remaining:])
    return selection


def rank_weekly(items: list[Item], limit: int | None = None) -> list[Item]:
    """Weekly digest ordering: P2 before P3, higher score first.

    ``limit`` is left unset by default -- the handover says the weekly cap is to
    be decided after watching one issue.
    """
    ranked = sorted(items, key=lambda i: (i.P or "P3", -i.score, i.date))
    return ranked[:limit] if limit else ranked
