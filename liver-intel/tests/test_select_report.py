from liver_intel.models import Item
from liver_intel.report import coverage_window, daily_markdown, weekly_markdown
from liver_intel.select import select_daily, rank_weekly


def make(priority, score, lines, n, title=None):
    return Item(src="newswire", title=title or f"{priority}-{n}",
                url=f"https://www.example.com/{priority}{n}", date="2026-09-14",
                meta={"src_kind": "company"}, lines=lines, P=priority, score=score)


def test_cap_of_ten_across_p0_p1_p2():
    items = ([make("P0", 9, ["L3"], i) for i in range(3)]
             + [make("P1", 6 - i * 0.1, ["L3"], i) for i in range(10)]
             + [make("P2", 3, ["L5"], i) for i in range(10)])
    selection = select_daily(items, cap=10)
    assert len(selection.daily) == 10
    assert selection.counts == {"P0": 3, "P1": 7}


def test_p0_is_never_capped():
    items = ([make("P0", 9, ["L3"], i) for i in range(14)]
             + [make("P1", 6, ["L3"], i) for i in range(5)])
    selection = select_daily(items, cap=10)
    assert len(selection.daily) == 14
    assert selection.counts == {"P0": 14}
    assert len(selection.weekly) == 5


def test_p3_never_enters_the_daily_report():
    items = [make("P3", 1, ["L3"], 1), make("P0", 9, ["L3"], 2)]
    selection = select_daily(items)
    assert [i.P for i in selection.daily] == ["P0"]
    assert [i.P for i in selection.weekly] == ["P3"]


def test_auxiliary_only_items_go_to_the_weekly_pool():
    items = [make("P1", 8, ["L9"], 1), make("P1", 7, ["L13"], 2),
             make("P1", 6, ["L3", "L13"], 3)]
    selection = select_daily(items)
    assert [i.title for i in selection.daily] == ["P1-3"]
    assert sorted(i.title for i in selection.weekly) == ["P1-1", "P1-2"]


def test_weekly_ranking_puts_p2_first():
    items = [make("P3", 1, ["L3"], 1), make("P2", 3, ["L3"], 2)]
    assert [i.P for i in rank_weekly(items)] == ["P2", "P3"]


def test_monday_covers_the_weekend():
    assert coverage_window("2026-09-14") == ("2026-09-11", "2026-09-14")


def test_other_weekdays_cover_the_previous_day():
    assert coverage_window("2026-09-15") == ("2026-09-14", "2026-09-15")


def test_daily_report_prints_url_and_quoted_evidence(domain_map):
    from liver_intel.models import Quote

    item = make("P0", 9, ["L3"], 1, title="Phase 3 topline")
    item.evidence.signals = ["PH3_RESULT"]
    item.evidence.quotes = [Quote(text="met the primary endpoint", locator="EX-99.1")]
    body = daily_markdown([item], "2026-09-14", domain_map, weekly_pool_size=4)
    assert item.url in body
    assert "> met the primary endpoint（EX-99.1）" in body
    assert "PH3_RESULT" in body
    assert "覆盖 2026-09-11 至 2026-09-14" in body
    assert "转入周汇总 4 条" in body


def test_daily_report_handles_an_empty_day(domain_map):
    body = daily_markdown([], "2026-09-15", domain_map)
    assert "无条目" in body


def test_chinese_quote_renders_its_translation(domain_map):
    from liver_intel.models import Quote

    item = make("P0", 9, ["L1"], 1, title="乙肝III期")
    item.evidence.quotes = [Quote(text="达到主要终点", lang="zh", translation="met the primary endpoint")]
    body = daily_markdown([item], "2026-09-14", domain_map)
    assert "> 达到主要终点" in body
    assert "译：met the primary endpoint" in body


def test_weekly_digest_lists_pool(domain_map):
    items = [make("P2", 3, ["L3"], 1), make("P3", 1, ["L9"], 2)]
    body = weekly_markdown(items, "2026-09-18", domain_map)
    assert "P2 1 条" in body and "P3 1 条" in body
    assert "https://www.example.com/P21" in body


def test_out_of_scope_items_are_dropped_not_pooled():
    """A wire's category feed carries every industry. A release matching no
    disease line is not liver intelligence, so it must not reach the digest."""
    items = [make("P3", 1, [], 1, title="Real estate grand opening"),
             make("P2", 3, ["L3"], 2, title="MASH trial update")]
    selection = select_daily(items)
    kept = [i.title for i in selection.daily + selection.weekly]
    assert kept == ["MASH trial update"]
    assert "Real estate grand opening" not in kept
    assert any("out of scope" in reason for _, reason in selection.dropped)
