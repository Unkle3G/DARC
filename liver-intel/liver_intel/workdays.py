"""Which days the daily run happens on.

The schedule is Monday to Friday, minus Chinese public holidays. That is not a
weekday test: the State Council moves holidays around and designates make-up
working days (调休), so the dates come from ``data/holidays_cn.json`` rather
than from a rule.

A year missing from that file is **not** an error. The run falls back to
Monday-Friday and says so loudly: an unnecessary run costs a few minutes of
API calls, while silently stopping the whole schedule because nobody updated a
file costs every day until someone notices.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .config import DATA_DIR

HOLIDAYS = DATA_DIR / "holidays_cn.json"
WEEKDAY_ZH = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


@dataclass
class Verdict:
    run: bool
    reason: str
    #: True when the year has no published calendar and the weekday rule was
    #: used instead. The runner prints this; a scheduled run must not fall
    #: back quietly.
    unverified: bool = False


@dataclass
class Workdays:
    off: frozenset[str]
    work: frozenset[str]
    years: frozenset[str]
    follow_makeup: bool = False

    @classmethod
    def load(cls, path: Path | str | None = None) -> "Workdays":
        path = Path(path or HOLIDAYS)
        if not path.exists():
            return cls(frozenset(), frozenset(), frozenset())
        raw = json.loads(path.read_text(encoding="utf-8"))
        years = raw.get("years", {}) or {}
        off: set[str] = set()
        work: set[str] = set()
        for entry in years.values():
            off.update(entry.get("off") or [])
            work.update(entry.get("work") or [])
        return cls(frozenset(off), frozenset(work), frozenset(years),
                   bool(raw.get("follow_makeup_workdays", False)))

    def verdict(self, day: str) -> Verdict:
        try:
            weekday = date.fromisoformat(day).weekday()
        except ValueError:
            return Verdict(False, f"{day} is not an ISO date")
        name = WEEKDAY_ZH[weekday]
        if day in self.off:
            return Verdict(False, f"{day}（{name}）法定节假日，不跑")
        if day in self.work:
            # A 调休 Saturday is a working day by decree, but the instruction
            # was Monday to Friday; the file's own switch decides.
            if weekday >= 5:
                return (Verdict(True, f"{day}（{name}）调休上班日")
                        if self.follow_makeup
                        else Verdict(False, f"{day}（{name}）调休上班日，按周一至周五的约定不跑"))
            return Verdict(True, f"{day}（{name}）工作日")
        if weekday >= 5:
            return Verdict(False, f"{day}（{name}）周末，不跑")
        if day[:4] not in self.years:
            return Verdict(True, f"{day}（{name}）工作日（{day[:4]} 年节假日表未录入，"
                                 f"按周一至周五执行；请更新 data/holidays_cn.json）",
                           unverified=True)
        return Verdict(True, f"{day}（{name}）工作日")


def verdict(day: str, path: Path | str | None = None) -> Verdict:
    return Workdays.load(path).verdict(day)
