"""WeChat Official Account (公众号) long-form rendering.

Reader-facing rules, distinct from the internal report:

* Triage grades (P0/P1/P2), line ids, adapter ids and signal names are
  **internal only**. Sections carry reading names (``config.SECTION_NAMES``) and
  each entry shows a row of searchable keywords instead of engine labels.
* Titles and quotes stay in the source language. A non-Chinese title is headed
  by its Chinese rendering with the original underneath; a non-Chinese quote is
  followed by its rendering. Both are marked 编者译，仅供参考. A Chinese source is
  shown as-is and never translated. When no rendering exists the original
  stands alone -- the engine does not invent one.
* Every entry ends with its own provenance line -- publisher, date and the
  clickable original link. The general sourcing note sits at the end.

The editor strips external stylesheets, so every rule here is inline. The
no-commentary rule from section 0 still holds: the prose adds counts, scope and
provenance, nothing else.
"""
from __future__ import annotations

import html as html_lib
from datetime import date

from .config import BRAND, SECTION_NAMES
from .conference import Calendar
from .domain_map import DomainMap
from .images import from_meta
from .keywords import reader_keywords
from .models import Item, is_chinese
from .report import WEEKDAY_ZH, coverage_window

INK = "#1a1a1a"
MUTED = "#8a8a8a"
RULE = "#e6e6e6"
ACCENT = "#9c2b2b"
LINK = "#576b95"          # WeChat's own link colour
WRAP = ("max-width:677px;margin:0 auto;padding:0 2px;color:%s;"
        "font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Hiragino Sans GB',"
        "'Microsoft YaHei',sans-serif;font-size:16px;line-height:1.75;"
        "letter-spacing:.02em;word-break:break-word;" % INK)
P = "margin:0 0 18px;font-size:16px;line-height:1.8;color:%s;" % INK
SMALL = "margin:0 0 10px;font-size:13px;line-height:1.7;color:%s;" % MUTED


def normalise(text: str) -> str:
    return " ".join((text or "").split()).strip().lower()


def esc(text: str) -> str:
    return html_lib.escape(str(text or ""), quote=False)


def _section(title: str, count: int | None = None) -> str:
    counter = ("" if count is None else
               f'<span style="margin-left:9px;font-size:13px;color:{MUTED};">{count} 条</span>')
    return (
        f'<section style="margin:38px 0 18px;">'
        f'<div style="display:flex;align-items:center;">'
        f'<span style="display:inline-block;width:4px;height:19px;background:{ACCENT};'
        f'margin-right:9px;"></span>'
        f'<span style="font-size:19px;font-weight:700;color:{INK};'
        f'letter-spacing:.04em;">{esc(title)}</span>'
        f'{counter}'
        f'</div>'
        f'<div style="height:1px;background:{RULE};margin-top:11px;"></div>'
        f'</section>')


def _entry_title(index: int, text: str, rendering: str = "") -> str:
    """Heading is the original; the Chinese rendering follows it.

    Original first, rendering after -- the same order as every quote. A
    Chinese title gets nothing added.
    """
    out = (f'<h2 style="margin:26px 0 8px;font-size:18px;line-height:1.55;'
           f'font-weight:700;color:{INK};">'
           f'<span style="color:{ACCENT};">{index:02d}</span>&nbsp;&nbsp;{esc(text)}</h2>')
    if rendering and not is_chinese(text):
        out += (f'<p style="margin:0 0 12px;font-size:15px;line-height:1.6;color:{INK};">'
                f'{esc(rendering)}'
                f'<span style="font-size:12px;color:{MUTED};">　编者译，仅供参考</span></p>')
    return out


def _keywords(item: Item, dm: DomainMap) -> str:
    tags = reader_keywords(item, dm)
    if not tags:
        return ""
    chips = "".join(
        f'<span style="display:inline-block;margin:0 6px 6px 0;padding:2px 9px;'
        f'border:1px solid {RULE};border-radius:2px;font-size:12px;color:{MUTED};'
        f'">{esc(tag)}</span>' for tag in tags)
    return f'<p style="margin:0 0 14px;line-height:2;">{chips}</p>'


def _quote(text: str, translation: str = "") -> str:
    block = (f'<blockquote style="margin:0 0 14px;padding:12px 16px;'
             f'border-left:3px solid {ACCENT};background:#faf7f7;font-size:15px;'
             f'line-height:1.8;color:{INK};">{esc(text)}')
    # Chinese sources are shown as written; only a foreign-language quote gets a
    # rendering, and it is labelled so no one mistakes it for the source text.
    if translation and not is_chinese(text):
        block += (f'<div style="margin-top:10px;padding-top:10px;'
                  f'border-top:1px dashed {RULE};color:{INK};font-size:14px;">'
                  f'{esc(translation)}'
                  f'<span style="color:{MUTED};font-size:12px;">'
                  f'　编者译，仅供参考</span></div>')
    return block + "</blockquote>"


def _figure(item: Item) -> str:
    figures = from_meta(item.meta)
    if not figures:
        return ""
    figure = figures[0]
    src = figure.local_path or figure.url
    caption = figure.alt or "源文档配图"
    return (
        f'<figure style="margin:0 0 16px;">'
        f'<img src="{esc(src)}" style="width:100%;max-width:677px;height:auto;'
        f'display:block;border-radius:2px;" alt="{esc(caption)}"/>'
        f'<figcaption style="margin-top:6px;font-size:12px;color:{MUTED};'
        f'text-align:center;">{esc(caption)}</figcaption></figure>')


#: Reader-facing names for where an item came from. Without these a registry or
#: literature entry falls back to its date alone, which tells a reader nothing.
SOURCE_NAMES = {
    "ctgov": "ClinicalTrials.gov",
    "edgar": "SEC EDGAR",
    "hkex": "香港交易所披露易",
    "cninfo": "巨潮资讯",
    "pubmed": "PubMed",
    "fda": "FDA",
    "ema": "EMA",
    "cn_regulator": "NMPA / CDE",
    "newsroom": "公司新闻室",
    "newswire": "通讯社",
    "society": "学会公告",
}


def provenance(item: Item) -> str:
    """Publisher line under an entry -- no adapter or line ids."""
    bits = []
    publisher = (item.meta.get("journal") or item.meta.get("regulator")
                 or item.meta.get("venue") or item.meta.get("wire")
                 or item.meta.get("society"))
    companies = item.meta.get("companies") or []
    if companies:
        bits.append("、".join(str(c) for c in companies[:2]))
    elif item.meta.get("sponsor"):
        # A registry record's issuer is its lead sponsor.
        bits.append(str(item.meta["sponsor"]))
    bits.append(str(publisher) if publisher else SOURCE_NAMES.get(item.src, item.src))
    bits.append(item.date)
    return " · ".join(b for b in bits if b)


def _field_value(quote) -> str:
    value = f'<span style="color:{INK};">{esc(quote.text)}</span>'
    if quote.translation and not is_chinese(quote.text):
        value += f'<span style="color:{MUTED};">｜{esc(quote.translation)}</span>'
    return value


def _source_line(item: Item) -> str:
    """Provenance directly under the entry: publisher, date, clickable original."""
    return (f'<p style="margin:0 0 6px;font-size:13px;line-height:1.7;color:{MUTED};">'
            f'出处：{esc(provenance(item))}　'
            f'<a href="{esc(item.url)}" style="color:{LINK};">查看原文 ↗</a></p>'
            f'<p style="margin:0 0 22px;font-size:12px;line-height:1.6;color:{MUTED};'
            f'word-break:break-all;">'
            f'<a href="{esc(item.url)}" style="color:{LINK};">{esc(item.url)}</a></p>')


def render_entry(item: Item, dm: DomainMap, index: int) -> str:
    parts = [_entry_title(index, item.title, str(item.meta.get("title_zh") or "")),
             _figure(item), _keywords(item, dm)]
    fields = [q for q in item.evidence.quotes if q.locator.startswith("ClinicalTrials.gov")]
    # The headline is already on the page; quoting it back as a pull quote
    # says nothing twice.
    prose = [q for q in item.evidence.quotes
             if q not in fields and normalise(q.text) != normalise(item.title)]
    if fields:
        # A registry states its facts in fields. Three one-word pull quotes read
        # as noise; the same values on one line read as a record. The lead
        # sponsor opens the record: "awaiting agreement with Sponsor" means
        # nothing until the sponsor is named.
        cells = []
        if item.meta.get("sponsor"):
            cells.append(f'leadSponsor：<span style="color:{INK};">'
                         f'{esc(item.meta["sponsor"])}</span>')
        cells += [f'{esc(q.locator.split("·")[-1].strip())}：{_field_value(q)}'
                  for q in fields[:4]]
        parts.append(f'<p style="{SMALL}">' + "　·　".join(cells) + "</p>")
    for quote in prose[:4]:
        parts.append(_quote(quote.text.strip(), quote.translation))
    if not item.evidence.quotes:
        parts.append(f'<p style="{SMALL}">本条未取得可核对的原文片段，详见原文链接。</p>')
    if item.meta.get("needs_human_read"):
        parts.append(f'<p style="{SMALL}">本条为公示列表变更，具体条目以原文为准。</p>')
    parts.append(_source_line(item))
    return "".join(parts)


def _sourcing_note(items: list[Item]) -> str:
    """The general note on where everything comes from; provenance itself sits
    under each entry."""
    out = [_section("信源说明")]
    out.append(
        f'<p style="{SMALL}">本刊条目全部来自一手源：公司公告、监管机构、临床试验注册平台、'
        f'同行评审期刊、专业学会，不采用媒体转述，不采用预印本。每条末尾标注出处与原文链接，'
        f'可点击直达。正文摘录均为原文逐字引用，保留原始语言；非中文条目的标题与摘录所附中文'
        f'均为编者译，仅供参考，以原文为准。中文内容仅作翻译与摘录，不作推断、不补背景、'
        f'不作评价。</p>')
    figures = [i for i in items if from_meta(i.meta)]
    if figures:
        out.append(
            f'<p style="{SMALL}">配图取自对应源文档自身发布的图片，版权归原发布方所有。</p>')
    return "".join(out)


def _conference_block(report_date: str, calendar: Calendar | None = None) -> str:
    """Reader-facing conference block: dates, and when abstracts go public."""
    calendar = calendar or Calendar.load()
    upcoming = calendar.upcoming(report_date, kind="annual")
    stcs = calendar.upcoming(report_date, kind="stc")
    if not upcoming and not stcs:
        return ""
    out = [_section("会议日历", len(upcoming) + len(stcs))]
    for conference, days in upcoming:
        when = "进行中" if days <= 0 else f"距开幕 {days} 天"
        place = f"　{conference.location}" if conference.location else ""
        if conference.location_zh:
            place += f"　{conference.location_zh}"
        out.append(
            f'<p style="margin:0 0 6px;font-size:16px;font-weight:600;color:{INK};">'
            f'{esc(conference.name)}'
            f'<span style="margin-left:8px;font-size:12px;font-weight:400;color:{ACCENT};">'
            f'{esc(when)}</span></p>')
        out.append(f'<p style="{SMALL}">会期 {esc(conference.start)} 至 '
                   f'{esc(conference.end)}{esc(place)}</p>')
        milestones = conference.milestones()
        if milestones:
            # One row per published date, in the order an author meets them;
            # a date the society has not published is simply absent.
            rows = "".join(
                f'<tr>'
                f'<td style="padding:3px 12px 3px 0;color:{MUTED};white-space:nowrap;'
                f'vertical-align:top;">{esc(m.label)}</td>'
                f'<td style="padding:3px 0;color:{INK if m.date >= report_date else MUTED};">'
                f'{esc(m.date)}{"" if m.date >= report_date else "　已过"}</td>'
                f'</tr>' for m in milestones)
            out.append(f'<table style="border-collapse:collapse;font-size:13px;'
                       f'line-height:1.7;margin:2px 0 16px;">{rows}</table>')
    if stcs:
        # Single-topic conferences: one line each, dates and place, nothing more.
        out.append(f'<p style="margin:18px 0 6px;font-size:14px;font-weight:600;'
                   f'color:{INK};">APASL 专题会（STC）</p>')
        rows = "".join(
            f'<tr>'
            f'<td style="padding:3px 12px 3px 0;color:{INK};vertical-align:top;">'
            f'{esc(c.name)}</td>'
            f'<td style="padding:3px 12px 3px 0;color:{MUTED};white-space:nowrap;'
            f'vertical-align:top;">{esc(c.start)} 至 {esc(c.end)}</td>'
            f'<td style="padding:3px 0;color:{MUTED};vertical-align:top;">'
            f'{esc(c.location)}'
            f'{"　" + esc(c.location_zh) if c.location_zh else ""}</td>'
            f'</tr>' for c, _ in stcs)
        out.append(f'<table style="border-collapse:collapse;font-size:13px;'
                   f'line-height:1.7;margin:0 0 16px;">{rows}</table>')
    return "".join(out)


def wechat_html(items: list[Item], report_date: str, dm: DomainMap,
                notes: list[str] | None = None, weekly_pool_size: int = 0,
                watermark: str = "", brand: str = BRAND,
                section_names: dict[str, str] | None = None) -> str:
    """One pasteable 公众号 article."""
    section_names = section_names or SECTION_NAMES
    start, end = coverage_window(report_date)
    weekday = WEEKDAY_ZH[date.fromisoformat(report_date).weekday()]
    coverage = f"{start} 至 {end}" if start != end else end

    out = [f'<div style="{WRAP}">']
    if watermark:
        out.append(
            f'<div style="margin:0 0 20px;padding:12px 14px;border:2px solid {ACCENT};'
            f'border-radius:2px;background:#fff5f5;font-size:14px;line-height:1.7;'
            f'color:{ACCENT};font-weight:700;">{esc(watermark)}</div>')

    out.append(f'<h1 style="margin:0 0 6px;font-size:22px;line-height:1.45;'
               f'font-weight:700;color:{INK};">{esc(brand)}｜{esc(report_date)}'
               f'（{esc(weekday)}）</h1>')
    out.append(f'<p style="{SMALL}">覆盖 {esc(coverage)}　·　本期 {len(items)} 条</p>')
    out.append(f'<div style="height:1px;background:{RULE};margin:16px 0 8px;"></div>')

    if not items:
        out.append(_section("本期", 0))
        out.append(f'<p style="{P}">今日一手源中没有达到入选门槛的条目。</p>')

    index = 0
    for priority in ("P0", "P1", "P2"):
        bucket = [i for i in items if i.P == priority]
        if not bucket:
            continue
        out.append(_section(section_names.get(priority, priority), len(bucket)))
        for item in bucket:
            index += 1
            out.append(render_entry(item, dm, index))

    out.append(_conference_block(report_date))

    if items:
        out.append(f'<div style="height:1px;background:{RULE};margin:34px 0 0;"></div>')
        out.append(_sourcing_note(items))

    # ``notes`` is accepted for call-site symmetry with the internal report and
    # deliberately not rendered: operator diagnostics are not reader copy.
    out.append("</div>")
    return "\n".join(out)
