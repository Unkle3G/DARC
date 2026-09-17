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


def test_conference_block_lists_only_verified_meetings(domain_map):
    from liver_intel.conference import Calendar, Conference
    from liver_intel.report import conference_lines

    calendar = Calendar(conferences=[
        Conference(id="A", name="Confirmed Meeting", start="2026-11-05",
                   end="2026-11-09", late_breaker_release="2026-11-05",
                   verified=True, source_url="https://example.org/dates"),
        Conference(id="B", name="Unconfirmed Meeting", verified=False),
    ])
    block = "\n".join(conference_lines("2026-09-17", calendar))
    assert "Confirmed Meeting" in block
    assert "还有 49 天" in block
    assert "2026-11-05" in block
    assert "Unconfirmed Meeting" not in block
    assert "待核实：B" in block


def test_a_finished_meeting_drops_out(domain_map):
    from liver_intel.conference import Calendar, Conference
    from liver_intel.report import conference_lines

    calendar = Calendar(conferences=[
        Conference(id="A", name="Past Meeting", start="2026-05-05", end="2026-05-09",
                   verified=True)])
    assert conference_lines("2026-09-17", calendar) == []


def test_conference_milestones_print_only_what_the_society_published(domain_map):
    from liver_intel.conference import Calendar, Conference
    from liver_intel.report import conference_lines
    calendar = Calendar(conferences=[Conference(
        id="X", name="Congress X", start="2026-11-05", end="2026-11-09", verified=True,
        location="Denver", abstract_close="2026-05-28", late_breaker_open="2026-09-15",
        late_breaker_close="2026-09-25", late_breaker_release="2026-11-05",
        source_url="https://example.org/x",
        date_sources={"late_breaker_close": "https://example.org/x/lba"})])
    text = "\n".join(conference_lines("2026-09-17", calendar))
    assert "摘要投稿截止：2026-05-28（已过）" in text
    assert "Late-breaker 投稿截止：2026-09-25（未到）  出处：https://example.org/x/lba" in text
    assert "Late-breaker embargo lift：2026-11-05" in text
    assert "未公布：摘要投稿开放、摘要录用通知、摘要 embargo lift、Late-breaker 录用通知" in text
    assert "此前处于禁发期" not in text


def test_single_topic_conferences_are_listed_but_open_no_window(domain_map):
    from liver_intel.conference import Calendar, Conference
    from liver_intel.report import conference_lines
    calendar = Calendar(conferences=[
        Conference(id="A", name="Annual", kind="annual", start="2026-11-05", end="2026-11-09",
                   verified=True),
        Conference(id="S", name="STC Kumamoto", kind="stc", start="2026-09-18", end="2026-09-19",
                   verified=True, location="Kumamoto", source_url="https://example.org/c")])
    assert calendar.active("2026-09-18") is None          # an STC never opens a window
    text = "\n".join(conference_lines("2026-09-17", calendar))
    assert "### APASL 专题会（STC）" in text
    assert "- STC Kumamoto：2026-09-18 至 2026-09-19（还有 1 天，Kumamoto）" in text
    assert text.index("**Annual**") < text.index("STC Kumamoto")


def test_upcoming_is_not_capped_by_default(domain_map):
    from liver_intel.conference import Calendar, Conference
    calendar = Calendar(conferences=[
        Conference(id=f"C{i}", name=f"C{i}", start=f"2027-0{i}-01", end=f"2027-0{i}-02", verified=True)
        for i in range(1, 7)])
    assert len(calendar.upcoming("2026-09-17")) == 6
