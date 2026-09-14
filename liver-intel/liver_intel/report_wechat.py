"""WeChat Official Account (公众号) long-form rendering.

Reader-facing rules, distinct from the internal report:

* Triage grades (P0/P1/P2), line ids, adapter ids and signal names are
  **internal only**. Sections carry reading names (``config.SECTION_NAMES``) and
  each entry shows a row of searchable keywords instead of engine labels.
* Quotes stay in the source language. A non-Chinese quote is followed by its
  Chinese rendering, marked 编者译，仅供参考. A Chinese quote is shown as-is and is
  never translated. When no translation exists the original stands alone -- the
  engine does not invent one.
* Sourcing notes, provenance and the clickable original links all sit at the
  end, as one reference list keyed to the entry numbers.

The editor strips external stylesheets, so every rule here is inline. The
no-commentary rule from section 0 still holds: the prose adds counts, scope and
provenance, nothing else.
"""
from __future__ import annotations

import html as html_lib
from datetime import date

from .config import BRAND, SECTION_NAMES
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


def esc(text: str) -> str:
    return html_lib.escape(str(text or ""), quote=False)


def _section(title: str, count: int) -> str:
    return (
        f'<section style="margin:38px 0 18px;">'
        f'<div style="display:flex;align-items:center;">'
        f'<span style="display:inline-block;width:4px;height:19px;background:{ACCENT};'
        f'margin-right:9px;"></span>'
        f'<span style="font-size:19px;font-weight:700;color:{INK};'
        f'letter-spacing:.04em;">{esc(title)}</span>'
        f'<span style="margin-left:9px;font-size:13px;color:{MUTED};">{count} 条</span>'
        f'</div>'
        f'<div style="height:1px;background:{RULE};margin-top:11px;"></div>'
        f'</section>')


def _entry_title(index: int, text: str) -> str:
    return (f'<h2 style="margin:26px 0 12px;font-size:18px;line-height:1.55;'
            f'font-weight:700;color:{INK};">'
            f'<span style="color:{ACCENT};">{index:02d}</span>&nbsp;&nbsp;{esc(text)}</h2>')


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


def _provenance(item: Item) -> str:
    """Publisher line for the reference list -- no adapter or line ids."""
    bits = []
    publisher = (item.meta.get("journal") or item.meta.get("regulator")
                 or item.meta.get("venue") or item.meta.get("wire"))
    companies = item.meta.get("companies") or []
    if companies:
        bits.append("、".join(str(c) for c in companies[:2]))
    if publisher:
        bits.append(str(publisher))
    bits.append(item.date)
    return " · ".join(bits)


def render_entry(item: Item, dm: DomainMap, index: int) -> str:
    parts = [_entry_title(index, item.title), _figure(item), _keywords(item, dm)]
    if item.evidence.quotes:
        for quote in item.evidence.quotes[:3]:
            parts.append(_quote(quote.text.strip(), quote.translation))
    else:
        parts.append(f'<p style="{SMALL}">本条未取得可核对的原文片段，详见文末原文链接。</p>')
    if item.meta.get("needs_human_read"):
        parts.append(f'<p style="{SMALL}">本条为公示列表变更，具体条目以原文为准。</p>')
    return "".join(parts)


def _reference_list(items: list[Item]) -> str:
    """Sourcing note plus every entry's provenance and clickable original link."""
    out = [_section("信源与原文", len(items))]
    out.append(
        f'<p style="{SMALL}">本刊条目全部来自一手源：公司公告、监管机构、临床试验注册平台、'
        f'同行评审期刊，不采用媒体转述，不采用预印本。正文摘录均为原文逐字引用，'
        f'保留原始语言；非中文条目所附中文为编者译，仅供参考，以原文为准。'
        f'中文内容仅作翻译与摘录，不作推断、不补背景、不作评价。</p>')
    for index, item in enumerate(items, start=1):
        out.append(
            f'<p style="{SMALL}">'
            f'<span style="color:{INK};font-weight:600;">{index:02d}</span>　'
            f'{esc(_provenance(item))}<br/>'
            f'<a href="{esc(item.url)}" style="color:{LINK};word-break:break-all;">'
            f'{esc(item.url)}</a></p>')
    figures = [i for i in items if from_meta(i.meta)]
    if figures:
        out.append(
            f'<p style="{SMALL}">配图取自对应源文档自身发布的图片，版权归原发布方所有。</p>')
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

    if items:
        out.append(f'<div style="height:1px;background:{RULE};margin:34px 0 0;"></div>')
        out.append(_reference_list(items))

    # ``notes`` is accepted for call-site symmetry with the internal report and
    # deliberately not rendered: operator diagnostics are not reader copy.
    out.append("</div>")
    return "\n".join(out)
