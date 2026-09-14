"""WeChat Official Account (公众号) long-form rendering.

The editor strips external stylesheets and <style> blocks, so every rule here is
an inline style, and the layout targets the editor's ~677px column while staying
readable on a phone.

The no-commentary rule from section 0 still holds. A 长文 layout gives the
material more room -- section headers, figures, pull quotes -- but it adds no
interpretation: the prose scaffolding is limited to counts, scope and provenance,
and everything substantive is either a field the engine extracted or text quoted
verbatim from the source. If you want analysis in the article, that is an
editorial pass by a human, not something this renderer will invent.
"""
from __future__ import annotations

import html as html_lib
from datetime import date

from .domain_map import DomainMap
from .grade import SIGNALS
from .images import from_meta
from .models import Item
from .report import WEEKDAY_ZH, coverage_window

# --- inline style constants ----------------------------------------------
INK = "#1a1a1a"
MUTED = "#8a8a8a"
RULE = "#e6e6e6"
ACCENT = "#9c2b2b"
WRAP = ("max-width:677px;margin:0 auto;padding:0 2px;color:%s;"
        "font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Hiragino Sans GB',"
        "'Microsoft YaHei',sans-serif;font-size:16px;line-height:1.75;"
        "letter-spacing:.02em;word-break:break-word;" % INK)
P = "margin:0 0 18px;font-size:16px;line-height:1.75;color:%s;" % INK
SMALL = "margin:0 0 10px;font-size:13px;line-height:1.7;color:%s;" % MUTED

PRIORITY_TITLES = {
    "P0": ("P0", "重大事件"),
    "P1": ("P1", "值得跟踪"),
    "P2": ("P2", "背景动态"),
}


def esc(text: str) -> str:
    return html_lib.escape(str(text or ""), quote=False)


def _h1(text: str) -> str:
    return (f'<h1 style="margin:0 0 6px;font-size:22px;line-height:1.45;'
            f'font-weight:700;color:{INK};">{esc(text)}</h1>')


def _section(label: str, subtitle: str) -> str:
    return (
        f'<section style="margin:36px 0 20px;">'
        f'<div style="display:inline-block;padding:3px 12px;border-radius:2px;'
        f'background:{ACCENT};color:#fff;font-size:13px;font-weight:700;'
        f'letter-spacing:.08em;">{esc(label)}</div>'
        f'<div style="margin-top:10px;font-size:19px;font-weight:700;color:{INK};">'
        f'{esc(subtitle)}</div>'
        f'<div style="height:2px;background:{RULE};margin-top:10px;"></div>'
        f'</section>')


def _entry_title(index: int, text: str) -> str:
    return (f'<h2 style="margin:28px 0 12px;font-size:18px;line-height:1.5;'
            f'font-weight:700;color:{INK};">'
            f'<span style="color:{ACCENT};">{index:02d}</span>&nbsp;&nbsp;{esc(text)}</h2>')


def _meta_row(label: str, value: str) -> str:
    return (f'<p style="{SMALL}"><span style="color:{INK};font-weight:600;">'
            f'{esc(label)}</span>　{esc(value)}</p>')


def _quote(text: str, locator: str = "", translation: str = "") -> str:
    tail = (f'<span style="color:{MUTED};font-size:12px;">　{esc(locator)}</span>'
            if locator else "")
    block = (f'<blockquote style="margin:0 0 14px;padding:12px 16px;'
             f'border-left:3px solid {ACCENT};background:#faf7f7;font-size:15px;'
             f'line-height:1.75;color:{INK};">{esc(text)}{tail}')
    if translation:
        block += (f'<div style="margin-top:8px;color:{MUTED};font-size:14px;">'
                  f'译：{esc(translation)}</div>')
    return block + "</blockquote>"


def _figure(item: Item) -> str:
    figures = from_meta(item.meta)
    if not figures:
        return ""
    figure = figures[0]
    src = figure.local_path or figure.url
    note = "本地文件，需在公众号编辑器中上传" if figure.local_path else "远端地址，公众号不支持外链图片，需先下载上传"
    caption = figure.alt or "源文档配图"
    return (
        f'<figure style="margin:0 0 16px;">'
        f'<img src="{esc(src)}" style="width:100%;max-width:677px;height:auto;'
        f'display:block;border-radius:2px;" alt="{esc(caption)}"/>'
        f'<figcaption style="margin-top:6px;font-size:12px;color:{MUTED};'
        f'text-align:center;">{esc(caption)}｜来源：{esc(figure.source_url or item.url)}'
        f'（{note}）</figcaption></figure>')


def _lines(item: Item, dm: DomainMap) -> str:
    labels = []
    for line_id in item.lines:
        line = dm.line(line_id)
        if line is None:
            labels.append(line_id)
            continue
        tier = "主线" if line.is_main else f"辅线×{dm.line_weight(line_id):g}"
        labels.append(f"{line_id} {line.name_zh}（{tier}）")
    return " / ".join(labels) or "—"


def _source(item: Item) -> str:
    bits = [item.meta.get("wire") or item.meta.get("venue")
            or item.meta.get("regulator") or item.meta.get("journal") or item.src]
    companies = item.meta.get("companies") or []
    if companies:
        bits.append("、".join(companies[:3]))
    bits.append(item.date)
    return " · ".join(str(b) for b in bits if b)


def render_entry(item: Item, dm: DomainMap, index: int) -> str:
    parts = [_entry_title(index, item.title), _figure(item)]
    parts.append(_meta_row("线路", _lines(item, dm)))
    parts.append(_meta_row("来源", _source(item)))
    if item.study:
        parts.append(_meta_row("研究标签", " / ".join(item.study)))
    signals = " / ".join(
        f"{s}（{SIGNALS[s]['desc']}）" if s in SIGNALS else s
        for s in item.evidence.signals) or "—"
    parts.append(_meta_row("命中信号", signals))

    if item.evidence.quotes:
        parts.append(f'<p style="{SMALL}"><span style="color:{INK};font-weight:600;">'
                     f'原文依据</span></p>')
        for quote in item.evidence.quotes[:3]:
            parts.append(_quote(quote.text.strip(), quote.locator, quote.translation))
    else:
        parts.append(_meta_row("原文依据", "未取得可核对的原文片段"))

    if item.meta.get("state_changes"):
        parts.append(_meta_row("状态变化", str(item.meta["state_changes"])))
    if item.meta.get("why_stopped"):
        parts.append(_meta_row("终止原因（原文）", str(item.meta["why_stopped"])))
    if item.meta.get("needs_human_read"):
        parts.append(_meta_row("备注", "列表页变更检测结果，需人工打开核对具体条目"))
    if item.meta.get("pub_date_is_future"):
        parts.append(_meta_row("备注", f"期刊标注发表日 {item.meta.get('pub_date')} "
                                       f"晚于采集日，按采集日归档"))

    parts.append(
        f'<p style="{SMALL}"><span style="color:{INK};font-weight:600;">原文链接</span>　'
        f'<span style="word-break:break-all;">{esc(item.url)}</span></p>')
    return "".join(parts)


def wechat_html(items: list[Item], report_date: str, dm: DomainMap,
                notes: list[str] | None = None, weekly_pool_size: int = 0,
                watermark: str = "") -> str:
    """One pasteable 公众号 article. ``watermark`` prints a banner at the top."""
    start, end = coverage_window(report_date)
    weekday = WEEKDAY_ZH[date.fromisoformat(report_date).weekday()]
    counts: dict[str, int] = {}
    for item in items:
        counts[item.P or "?"] = counts.get(item.P or "?", 0) + 1
    summary = "／".join(f"{p} {counts[p]} 条" for p in sorted(counts)) or "无条目"
    coverage = f"{start} 至 {end}" if start != end else end

    out = [f'<div style="{WRAP}">']

    if watermark:
        out.append(
            f'<div style="margin:0 0 20px;padding:12px 14px;border:2px solid {ACCENT};'
            f'border-radius:2px;background:#fff5f5;font-size:14px;line-height:1.7;'
            f'color:{ACCENT};font-weight:700;">{esc(watermark)}</div>')

    out.append(_h1(f"肝病情报日报｜{report_date}（{weekday}）"))
    out.append(f'<p style="{SMALL}">覆盖 {esc(coverage)}　·　本期 {esc(summary)}'
               f'　·　转入周汇总 {weekly_pool_size} 条</p>')
    out.append(f'<div style="height:1px;background:{RULE};margin:18px 0 24px;"></div>')

    out.append(f'<p style="{P}">本期条目全部来自一手源：公司公告、监管机构、'
               f'临床试验注册平台、同行评审期刊。不采用媒体转述，不采用预印本。'
               f'中文内容仅做翻译与摘录，不作推断、不补背景、不作评价。'
               f'每条均附原始链接，可逐条回溯核对。</p>')

    if not items:
        out.append(_section("本期", "无条目"))
        out.append(f'<p style="{P}">今日一手源中没有达到入选门槛的条目。</p>')

    index = 0
    for priority in ("P0", "P1", "P2"):
        bucket = [i for i in items if i.P == priority]
        if not bucket:
            continue
        label, subtitle = PRIORITY_TITLES[priority]
        out.append(_section(label, f"{subtitle}（{len(bucket)} 条）"))
        for item in bucket:
            index += 1
            out.append(render_entry(item, dm, index))

    out.append(f'<div style="height:1px;background:{RULE};margin:32px 0 18px;"></div>')
    out.append(_section("附", "口径说明"))
    out.append(f'<p style="{SMALL}">分级：P0 不设上限；P0+P1+P2 合计不超过 10 条；'
               f'P3 只进周汇总。辅线（L9–L16）权重 0.5，未命中主线不进日报；'
               f'L13（AI）权重 1.0，但仍属辅线。</p>')
    out.append(f'<p style="{SMALL}">配图均取自源文档自身发布的图片，并标注来源地址；'
               f'是否可再使用需按各自版权条款自行判断。公众号编辑器不支持外链图片，'
               f'需先下载再上传至素材库。</p>')
    if notes:
        out.append(f'<p style="{SMALL}"><span style="color:{INK};font-weight:600;">'
                   f'运行提示</span></p>')
        for note in notes:
            out.append(f'<p style="{SMALL}">· {esc(note)}</p>')
    out.append("</div>")
    return "\n".join(out)
