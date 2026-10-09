"""周一至周五，节假日不跑 -- and a missing year must not stop the schedule."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from liver_intel import pipeline
from liver_intel.workdays import Workdays, verdict


@pytest.fixture
def calendar(tmp_path):
    path = tmp_path / "holidays.json"
    path.write_text(json.dumps({
        "follow_makeup_workdays": False,
        "years": {"2026": {"off": ["2026-10-01", "2026-10-02", "2026-02-16"],
                           "work": ["2026-09-20", "2026-01-04"]}},
    }, ensure_ascii=False), encoding="utf-8")
    return path


def test_a_weekday_runs(calendar):
    assert verdict("2026-09-17", calendar).run is True


def test_a_weekend_does_not(calendar):
    assert verdict("2026-09-19", calendar).run is False
    assert "周末" in verdict("2026-09-19", calendar).reason


def test_a_public_holiday_does_not_even_on_a_weekday(calendar):
    decision = verdict("2026-10-01", calendar)      # a Thursday
    assert decision.run is False and "法定节假日" in decision.reason


def test_a_makeup_working_weekend_follows_the_switch(calendar):
    """A 调休 Saturday is a working day by decree, but the instruction was
    Monday to Friday, so the file's own switch decides."""
    assert verdict("2026-09-20", calendar).run is False
    on = replace(Workdays.load(calendar), follow_makeup=True)
    assert on.verdict("2026-09-20").run is True


def test_a_year_with_no_published_calendar_falls_back_loudly(calendar):
    """Losing every day of intelligence because nobody updated a file is worse
    than one unnecessary run -- but it must never be silent."""
    decision = verdict("2027-03-01", calendar)      # a Monday
    assert decision.run is True and decision.unverified is True
    assert "未录入" in decision.reason and "2027" in decision.reason


def test_a_missing_file_still_runs_on_weekdays(tmp_path):
    assert verdict("2026-09-17", tmp_path / "absent.json").run is True
    assert verdict("2026-09-19", tmp_path / "absent.json").run is False


def test_the_shipped_calendar_carries_the_state_council_paper():
    data = json.loads((pipeline.Settings().data_dir / "holidays_cn.json")
                      .read_text(encoding="utf-8"))
    year = data["years"]["2026"]
    assert year["papers"] and all(p.startswith("https://www.gov.cn/") for p in year["papers"])
    assert "2026-10-01" in year["off"] and "2026-02-16" in year["off"]
