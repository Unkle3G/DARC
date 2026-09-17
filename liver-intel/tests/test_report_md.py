"""The MDNice Markdown article: plain Markdown, same rules as the HTML one."""
from __future__ import annotations

import re

from liver_intel.conference import Calendar, Conference
from liver_intel.models import Item, Quote
from liver_intel.report_md import conference_block, wechat_markdown


def make(priority="P0", lines=("L3",), title="Phase 3 topline", study=("PHASE3",), **meta):
    item = Item(src="newswire", title=title, url="https://www.example.com/a",
                date="2026-09-14", meta={"src_kind": "company", **meta},
                lines=list(lines), study=list(study), P=priority, score=9.2)
    item.evidence.signals = ["PH3_RESULT"]
    item.evidence.quotes = [Quote(text="met the primary endpoint", locator="para 1")]
    return item


def body_of(md: str) -> str:
    return md.split("## 信源说明")[0]


def test_article_carries_no_html(domain_map):
    """MDNice themes Markdown; inline styles would survive it and clash."""
    md = wechat_markdown([make()], "2026-09-14", domain_map)
    assert "<" not in md and "style=" not in md


def test_internal_grades_never_reach_the_reader(domain_map):
    item = make()
    item.meta["wire"] = "GlobeNewswire"
    md = wechat_markdown([item], "2026-09-14", domain_map)
    for internal in ("P0", "P1", "P2", "PH3_RESULT", "newswire", "score"):
        assert internal not in md, internal


def test_sections_use_reading_names_and_heading_levels(domain_map):
    items = [make("P0", title="a"), make("P1", title="b"), make("P2", title="c")]
    md = wechat_markdown(items, "2026-09-14", domain_map)
    assert "# HepaDaily｜2026-09-14（周一）" in md
    assert "## 今日头条" in md and "## 前沿速览" in md and "## 最新动态" in md
    assert re.search(r"^### 01 a$", md, re.M)


def test_keywords_are_code_spans(domain_map):
    """Code spans are what MDNice themes render as chips."""
    item = make()
    item.meta["companies"] = ["Madrigal Pharmaceuticals"]
    md = wechat_markdown([item], "2026-09-14", domain_map)
    assert "`MASH`" in md and "`Madrigal Pharmaceuticals`" in md and "`III期临床`" in md


def test_original_title_first_then_the_rendering(domain_map):
    item = make(title="Phase 3 topline results in MASH")
    item.meta["title_zh"] = "MASH Phase 3 顶线结果"
    md = wechat_markdown([item], "2026-09-14", domain_map)
    assert "### 01 Phase 3 topline results in MASH" in md
    assert md.index("### 01 Phase 3 topline") < md.index("MASH Phase 3 顶线结果")
    assert "*编者译，仅供参考*" in body_of(md)


def test_chinese_title_gets_nothing_added(domain_map):
    item = make(title="某公司Ⅲ期临床达到主要终点")
    item.meta["title_zh"] = "should not appear"
    md = wechat_markdown([item], "2026-09-14", domain_map)
    assert "should not appear" not in md


def test_quote_is_a_blockquote_with_its_rendering_under_it(domain_map):
    item = make()
    item.evidence.quotes = [Quote(text="met the primary endpoint", lang="en",
                                  translation="达到主要终点")]
    md = wechat_markdown([item], "2026-09-14", domain_map)
    assert "> met the primary endpoint" in md
    assert "> 达到主要终点　*编者译，仅供参考*" in md
    assert md.index("> met the primary") < md.index("> 达到主要终点")


def test_chinese_quote_is_never_translated(domain_map):
    item = make()
    item.evidence.quotes = [Quote(text="该方案达到主要终点", lang="zh",
                                  translation="met the primary endpoint")]
    md = wechat_markdown([item], "2026-09-14", domain_map)
    assert "> 该方案达到主要终点" in md
    assert "met the primary endpoint" not in md


def test_registry_record_is_one_line_opening_with_the_sponsor(domain_map):
    item = make()
    item.src = "ctgov"
    item.meta["sponsor"] = "Roswell Park Cancer Institute"
    item.evidence.quotes = [
        Quote(text="SUSPENDED", locator="ClinicalTrials.gov · overallStatus",
              translation="已暂停"),
        Quote(text="awaiting agreement with Sponsor",
              locator="ClinicalTrials.gov · whyStopped", translation="等待与申办方达成协议")]
    md = wechat_markdown([item], "2026-09-14", domain_map)
    record = next(l for l in md.splitlines() if "leadSponsor" in l)
    assert record.startswith("**leadSponsor**：Roswell Park Cancer Institute")
    assert "**overallStatus**：SUSPENDED｜已暂停" in record
    assert "> SUSPENDED" not in md          # a field is not a pull quote


def test_each_entry_carries_its_own_source_link(domain_map):
    items = [make("P0", title="first"), make("P1", title="second")]
    for index, item in enumerate(items):
        item.url = f"https://www.example.com/{index}"
        item.meta["wire"] = "GlobeNewswire"
    md = wechat_markdown(items, "2026-09-14", domain_map)
    assert "出处：GlobeNewswire · 2026-09-14　[查看原文](https://www.example.com/0)" in md
    assert md.index("https://www.example.com/0") < md.index("### 02 second")


def test_markdown_syntax_in_source_text_is_escaped(domain_map):
    item = make(title="Drug_X *and* [brackets]")
    md = wechat_markdown([item], "2026-09-14", domain_map)
    assert r"Drug\_X \*and\* \[brackets\]" in md


def test_a_heading_marker_in_a_title_cannot_become_a_heading(domain_map):
    item = make()
    item.evidence.quotes = [Quote(text="# not a heading", locator="para 1")]
    md = wechat_markdown([item], "2026-09-14", domain_map)
    assert "> \\# not a heading" in md


def test_conference_calendar_is_a_table(domain_map):
    calendar = Calendar(conferences=[
        Conference(id="A", name="Annual X", kind="annual", start="2026-11-05",
                   end="2026-11-09", verified=True, location="Denver", location_zh="美国 丹佛",
                   abstract_close="2026-05-28", late_breaker_release="2026-11-05"),
        Conference(id="S", name="STC Almaty", kind="stc", start="2026-10-08", end="2026-10-09",
                   verified=True, location="Almaty, Kazakhstan",
                   location_zh="哈萨克斯坦 阿拉木图")])
    md = "\n".join(conference_block("2026-09-17", calendar))
    assert "| 摘要投稿截止 | 2026-05-28 | 已过 |" in md
    assert "| Late-breaker embargo lift | 2026-11-05 | 未到 |" in md
    assert "**APASL 专题会（STC）**" in md
    assert "| STC Almaty | 2026-10-08 至 2026-10-09 | Almaty, Kazakhstan　哈萨克斯坦 阿拉木图 |" in md
    assert "Denver　美国 丹佛" in md


def test_operator_notes_never_reach_the_reader(domain_map):
    md = wechat_markdown([make()], "2026-09-14", domain_map,
                         notes=["[newswire] no verified endpoint in the registry"])
    assert "no verified endpoint" not in md and "运行提示" not in md


def test_empty_day_still_renders(domain_map):
    md = wechat_markdown([], "2026-09-15", domain_map)
    assert "没有达到入选门槛" in md
