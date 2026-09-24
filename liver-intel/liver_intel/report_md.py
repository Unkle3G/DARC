"""公众号 article as Markdown, for pasting into MDNice (墨滴).

The HTML renderer styles every element itself. MDNice does that job instead --
you paste Markdown, pick a theme, and it produces the styled HTML WeChat wants.
So this renderer emits **plain Markdown and no HTML**: inline styles would
survive MDNice untouched and clash with the theme, and a raw ``<div>`` is not
themed at all.

Three MDNice conventions this leans on:

* inline code spans are what its themes render as chips, so the reader-facing
  keywords are code spans rather than a hand-built row;
* tables are themed, so the conference calendar is a table;
* links stay ordinary Markdown links. WeChat strips external links from the
  body, and MDNice's "微信外链转脚注" option turns them into numbered footnotes
  at the end -- which is exactly the provenance the article needs, so nothing
  here tries to solve it.

Everything else matches the HTML renderer: no grades, no line ids, no adapter
names; original first with the rendering after it; each entry carries its own
provenance; the sourcing note sits at the end.
"""
from __future__ import annotations

from datetime import date

from .config import BRAND, SECTION_NAMES
from .conference import Calendar
from .domain_map import DomainMap
from .images import from_meta
from .keywords import reader_keywords
from .models import Item, is_chinese
from .report import WEEKDAY_ZH, coverage_window, issue_label
from .report_wechat import journal_record, normalise, provenance

TRANSLATED = "编者译，仅供参考"

#: Characters that would otherwise be read as Markdown syntax at the start of a
#: line. A title beginning "# " or "> " is rare but not impossible, and a
#: silently swallowed heading marker changes what the reader sees.
_LINE_STARTERS = ("#", ">", "-", "+", "*", "|", "=")


def esc(text: str) -> str:
    """Neutralise Markdown syntax inside text lifted from a source document."""
    out = (text or "").replace("\\", "\\\\")
    for char in ("*", "_", "`", "[", "]"):
        out = out.replace(char, "\\" + char)
    return out


def _line(text: str) -> str:
    """One paragraph of source text, safe at the start of a line."""
    body = esc(text).strip()
    return ("\\" + body) if body[:1] in _LINE_STARTERS else body


def _entry_heading(index: int, item: Item) -> list[str]:
    """Original title as the heading; the Chinese rendering follows it."""
    out = [f"### {index:02d} {_line(item.title)}", ""]
    rendering = str(item.meta.get("title_zh") or "")
    if rendering and not is_chinese(item.title):
        out += [f"{_line(rendering)}　*{TRANSLATED}*", ""]
    return out


def _keywords(item: Item, dm: DomainMap) -> list[str]:
    tags = reader_keywords(item, dm)
    if not tags:
        return []
    return [" ".join(f"`{tag}`" for tag in tags), ""]


def _figure(item: Item) -> list[str]:
    figures = from_meta(item.meta)
    if not figures:
        return []
    figure = figures[0]
    return [f"![{esc(figure.alt or '源文档配图')}]({figure.local_path or figure.url})", ""]


def _record(item: Item, fields: list) -> list[str]:
    """A record's published field values -- registry or journal.

    One fact per line. Six fields joined by interpuncts wrapped into a grey
    slab that no one could scan; a list gives each value its own line and its
    own left edge.
    """
    cells = [f"**{esc(label)}**：{esc(value)}" for label, value in journal_record(item)]
    if item.meta.get("sponsor"):
        cells.append(f"**leadSponsor**：{esc(str(item.meta['sponsor']))}")
    for quote in fields[:6]:
        label = esc(quote.locator.split("·")[-1].strip())
        value = esc(quote.text)
        if quote.translation and not is_chinese(quote.text):
            value += f"｜{esc(quote.translation)}"
        cells.append(f"**{label}**：{value}")
    return [f"- {cell}" for cell in cells] + [""] if cells else []


def _quote(quote) -> list[str]:
    """Blockquote: the source's own words, then the rendering under them."""
    out = [f"> {_line(quote.text)}"]
    if quote.translation and not is_chinese(quote.text):
        out += [">", f"> {_line(quote.translation)}　*{TRANSLATED}*"]
    out.append("")
    return out


def _source(item: Item) -> list[str]:
    return [f"出处：{esc(provenance(item))}　[查看原文]({item.url})", ""]


def render_entry(item: Item, dm: DomainMap, index: int) -> list[str]:
    out = _entry_heading(index, item) + _figure(item) + _keywords(item, dm)
    fields = [q for q in item.evidence.quotes if q.locator.startswith("ClinicalTrials.gov")]
    prose = [q for q in item.evidence.quotes
             if q not in fields and normalise(q.text) != normalise(item.title)]
    out += _record(item, fields)
    for quote in prose[:4]:
        out += _quote(quote)
    if not item.evidence.quotes:
        out += ["本条未取得可核对的原文片段，详见原文链接。", ""]
    if item.meta.get("needs_human_read"):
        out += ["本条为公示列表变更，具体条目以原文为准。", ""]
    return out + _source(item)


def conference_block(report_date: str, calendar: Calendar | None = None) -> list[str]:
    """The calendar as themed tables: annual meetings in full, STCs one row each."""
    calendar = calendar or Calendar.load()
    upcoming = calendar.upcoming(report_date, kind="annual")
    stcs = calendar.upcoming(report_date, kind="stc")
    if not upcoming and not stcs:
        return []
    out = ["## 会议日历", ""]
    for conference, days in upcoming:
        when = "进行中" if days <= 0 else f"距开幕 {days} 天"
        place = "　".join(filter(None, [conference.location, conference.location_zh]))
        out += [f"**{esc(conference.name)}**　{when}", ""]
        out += [f"会期 {conference.start} 至 {conference.end}"
                + (f"　{esc(place)}" if place else ""), ""]
        milestones = conference.milestones()
        if milestones:
            out += ["| 关键日期 | 日期 | 状态 |", "| --- | --- | --- |"]
            out += [f"| {esc(m.label)} | {m.date} | "
                    f"{'未到' if m.date >= report_date else '已过'} |" for m in milestones]
            out.append("")
    if stcs:
        out += ["**APASL 专题会（STC）**", ""]
        out += ["| 会议 | 会期 | 地点 |", "| --- | --- | --- |"]
        for conference, _ in stcs:
            place = "　".join(filter(None, [conference.location, conference.location_zh]))
            out.append(f"| {esc(conference.name)} | {conference.start} 至 "
                       f"{conference.end} | {esc(place)} |")
        out.append("")
    return out


def sourcing_note(items: list[Item]) -> list[str]:
    out = ["## 信源说明", "",
           "本刊条目全部来自一手源：公司公告、监管机构、临床试验注册平台、同行评审期刊、"
           "专业学会，不采用媒体转述，不采用预印本。每条末尾标注出处与原文链接。"
           "正文摘录均为原文逐字引用，保留原始语言；非中文条目的标题与摘录所附中文均为"
           f"{TRANSLATED}，以原文为准。中文内容仅作翻译与摘录，不作推断、不补背景、不作评价。",
           ""]
    if any(from_meta(i.meta) for i in items):
        out += ["配图取自对应源文档自身发布的图片，版权归原发布方所有。", ""]
    return out


def wechat_markdown(items: list[Item], report_date: str, dm: DomainMap,
                    notes: list[str] | None = None, weekly_pool_size: int = 0,
                    watermark: str = "", brand: str = BRAND,
                    section_names: dict[str, str] | None = None,
                    issue: str | None = None, summary: str | None = None) -> str:
    """One pasteable MDNice document.

    ``notes`` is accepted for call-site symmetry with the other renderers and
    deliberately not rendered: operator diagnostics are not reader copy.
    """
    section_names = section_names or SECTION_NAMES
    start, end = coverage_window(report_date)
    weekday = WEEKDAY_ZH[date.fromisoformat(report_date).weekday()]
    coverage = f"{start} 至 {end}" if start != end else end
    label = issue_label(issue)
    masthead = f"{brand}｜{label}｜{report_date}" if label else f"{brand}｜{report_date}"

    out: list[str] = []
    if watermark:
        out += [f"> **{esc(watermark)}**", ""]
    out += [f"# {esc(masthead)}（{weekday}）", "",
            f"> 覆盖 {coverage}　·　本期 {len(items)} 条", ""]

    # Before the entries. Plain paragraph text: a blockquote here would collide
    # with the coverage line above it under most MDNice themes.
    if summary and summary.strip():
        out += [esc(summary.strip()), ""]

    if not items:
        out += ["## 本期", "", "今日一手源中没有达到入选门槛的条目。", ""]

    index = 0
    for priority in ("P0", "P1", "P2"):
        bucket = [i for i in items if i.P == priority]
        if not bucket:
            continue
        out += [f"## {section_names.get(priority, priority)}", ""]
        for item in bucket:
            if index:
                # A rule between entries: without one the next entry's heading
                # runs straight on from the previous entry's provenance line.
                out += ["---", ""]
            index += 1
            out += render_entry(item, dm, index)

    out += conference_block(report_date)
    if items:
        out += ["---", ""] + sourcing_note(items)
    return "\n".join(out).rstrip() + "\n"
