"""Report rendering.

Daily report (Mon-Fri) and the Friday weekly digest.

The handover pointed at ``liver_daily_2026-09-14.md`` as the format of record;
that file was not attached to the implementation session, so this layout is
rebuilt from the field contract in section 2 and marked here so it can be
swapped for the real one without touching the pipeline.

Two rules the renderer enforces rather than assumes:

* every entry prints its原始 URL;
* evidence is printed as quoted source text, never as a summary of it.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from .domain_map import DomainMap
from .grade import SIGNALS
from .models import Item

WEEKDAY_ZH = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def coverage_window(report_date: str) -> tuple[str, str]:
    """What a given day's report covers.

    Monday reaches back through the weekend (Friday to Sunday); every other
    weekday covers the previous day.
    """
    day = date.fromisoformat(report_date)
    back = 3 if day.weekday() == 0 else 1
    return (day - timedelta(days=back)).isoformat(), report_date


def _line_labels(item: Item, dm: DomainMap) -> str:
    labels = []
    for line_id in item.lines:
        line = dm.line(line_id)
        if line is None:
            labels.append(line_id)
            continue
        tier = "主线" if line.is_main else f"辅线×{dm.line_weight(line_id):g}"
        labels.append(f"{line_id} {line.name_zh}（{tier}）")
    return " / ".join(labels) if labels else "—"


def _source_line(item: Item) -> str:
    bits = [item.meta.get("wire") or item.meta.get("venue")
            or item.meta.get("regulator") or item.meta.get("journal") or item.src]
    companies = item.meta.get("companies") or []
    if companies:
        bits.append(" · ".join(companies[:3]))
    bits.append(item.date)
    if item.meta.get("conference"):
        bits.append(str(item.meta["conference"]))
    return " · ".join(str(b) for b in bits if b)


def _signals_line(item: Item) -> str:
    signals = item.evidence.signals or []
    if not signals:
        return "—"
    return " / ".join(
        f"{s}（{SIGNALS[s]['desc']}）" if s in SIGNALS else s for s in signals)


def _evidence_block(item: Item) -> list[str]:
    if not item.evidence.quotes:
        return ["- 原文依据：未取得可核对的原文片段"]
    out = ["- 原文依据："]
    for quote in item.evidence.quotes[:4]:
        locator = f"（{quote.locator}）" if quote.locator else ""
        out.append(f"  > {quote.text.strip()}{locator}")
        if quote.translation:
            out.append(f"  > 译：{quote.translation.strip()}")
    return out


def render_item(item: Item, dm: DomainMap, index: int) -> str:
    lines = [
        f"### {index}. {item.title}",
        "",
        f"- 线：{_line_labels(item, dm)}",
        f"- 来源：{_source_line(item)}",
        f"- 链接：{item.url}",
    ]
    if item.study:
        lines.append(f"- 研究标签：{' / '.join(item.study)}")
    lines.append(f"- 命中信号：{_signals_line(item)}")
    lines.extend(_evidence_block(item))
    if item.meta.get("state_changes"):
        lines.append(f"- 状态变化：{item.meta['state_changes']}")
    if item.meta.get("why_stopped"):
        lines.append(f"- 终止原因（原文）：{item.meta['why_stopped']}")
    if item.meta.get("needs_human_read"):
        lines.append("- 备注：该条为列表页变更检测结果，需人工打开核对具体条目")
    if item.meta.get("pub_date_is_future"):
        lines.append(f"- 备注：期刊标注发表日 {item.meta.get('pub_date')} 晚于采集日，按采集日归档")
    lines.append(f"- score：{item.score:g}")
    return "\n".join(lines)


def daily_markdown(items: list[Item], report_date: str, dm: DomainMap,
                   notes: Iterable[str] = (), weekly_pool_size: int = 0) -> str:
    start, end = coverage_window(report_date)
    weekday = WEEKDAY_ZH[date.fromisoformat(report_date).weekday()]
    counts: dict[str, int] = {}
    for item in items:
        counts[item.P or "?"] = counts.get(item.P or "?", 0) + 1

    coverage = (f"覆盖 {start} 至 {end}" if start != end
                else f"覆盖 {end}")
    head = [
        f"# 肝病情报日报 {report_date}（{weekday}，{coverage}）",
        "",
        f"> 本期 {' / '.join(f'{p} {counts[p]} 条' for p in sorted(counts)) or '无条目'}"
        f"；转入周汇总 {weekly_pool_size} 条。",
        "> 采集范围：公司公告、监管机构、临床试验注册、同行评审期刊。不采媒体转述与预印本。",
        "> 中文条目仅做翻译与摘录，不作推断、不补背景、不作评价。",
        "",
    ]

    body: list[str] = []
    index = 0
    for priority in ("P0", "P1", "P2"):
        bucket = [i for i in items if i.P == priority]
        if not bucket:
            continue
        body.append(f"## {priority}")
        body.append("")
        for item in bucket:
            index += 1
            body.append(render_item(item, dm, index))
            body.append("")

    if not items:
        body.append("## 无条目")
        body.append("")
        body.append("今日一手源无达到入选门槛的条目。")
        body.append("")

    tail: list[str] = []
    note_list = [n for n in notes]
    if note_list:
        tail.append("## 运行提示")
        tail.append("")
        tail.extend(f"- {note}" for note in note_list)
        tail.append("")
    return "\n".join(head + body + tail).rstrip() + "\n"


def weekly_markdown(items: list[Item], report_date: str, dm: DomainMap,
                    notes: Iterable[str] = ()) -> str:
    day = date.fromisoformat(report_date)
    start = (day - timedelta(days=4)).isoformat()
    head = [
        f"# 肝病情报周汇总 {report_date}（覆盖 {start} 至 {report_date}）",
        "",
        f"> 收录本周未进入每日报的条目，共 {len(items)} 条"
        f"（P2 {sum(1 for i in items if i.P == 'P2')} 条 / "
        f"P3 {sum(1 for i in items if i.P == 'P3')} 条）。",
        "> 上限未设：按handover要求观察一期后再定。",
        "",
    ]
    body: list[str] = []
    index = 0
    for priority in ("P2", "P3"):
        bucket = [i for i in items if i.P == priority]
        if not bucket:
            continue
        body.append(f"## {priority}")
        body.append("")
        for item in bucket:
            index += 1
            body.append(f"{index}. [{item.title}]({item.url}) — "
                        f"{_line_labels(item, dm)} · {_source_line(item)}")
        body.append("")
    if not items:
        body.append("本周无转入条目。")
        body.append("")
    tail: list[str] = []
    note_list = list(notes)
    if note_list:
        tail.append("## 运行提示")
        tail.append("")
        tail.extend(f"- {note}" for note in note_list)
    return "\n".join(head + body + tail).rstrip() + "\n"
