"""Renderings by the operator's own Claude session, not by an API call.

The engine has no credential of its own on most machines and the operator
does not want one. What it does have is a Claude Code session driving it. So
the daily run writes a *worksheet*: every non-Chinese title, quote and field
value that shipped, each with an empty ``zh`` slot. The session fills the
slots -- under the same rules the API step would follow -- and ``render``
reads them back through the same acceptance checks (must be Chinese, must
differ from the source, no evaluative wording) before anything reaches the
article. The engine never invents a rendering; an unfilled slot leaves the
original standing alone.

The worksheet is plain JSON so it survives a move to any machine, and the
rules travel inside it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Item
from .translate import _accept, needs_rendering

RULES = (
    "把每个 text 逐字、完整地译成简体中文，填入 zh。",
    "药物代号、化合物名、试验名称、公司名、基因/靶点名、注册号、期刊名保持原样，不译。",
    "只译原文写了的内容：不加背景、不加解释、不加术语注释、不加括号说明。",
    "不预测、不评价、不排序，不使用利好/利空/有望/重磅之类的措辞。",
    "拿不准的留空（zh 保持 \"\"），引擎会让原文单独呈现；宁缺毋滥。",
    "不要改动 text、id 或其他字段。",
)


def _slots(item: Item) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if needs_rendering(item.title) and not item.meta.get("title_zh"):
        out.append({"id": f"{item.key}:title", "kind": "title", "text": item.title, "zh": ""})
    for index, quote in enumerate(item.evidence.quotes):
        if needs_rendering(quote.text) and not quote.translation:
            kind = "field" if quote.locator.startswith("ClinicalTrials.gov") else "quote"
            out.append({"id": f"{item.key}:quote:{index}", "kind": kind,
                        "locator": quote.locator, "text": quote.text, "zh": ""})
    return out


def write(items: list[Item], path: Path, report_date: str) -> int:
    """Write the worksheet; return how many slots it holds."""
    entries = []
    for item in items:
        for slot in _slots(item):
            slot["item_title"] = item.title
            entries.append(slot)
    payload = {"date": report_date, "rules": list(RULES), "entries": entries}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(entries)


def apply(items: list[Item], path: Path) -> tuple[int, int, list[str]]:
    """Read a filled worksheet onto the items.

    Returns (accepted, rejected, reasons). A slot is rejected, not published,
    when its rendering fails the same checks the API step's output goes
    through; the reason names the slot so the operator can fix it.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    by_key = {item.key: item for item in items}
    accepted = rejected = 0
    reasons: list[str] = []
    for entry in payload.get("entries", []):
        rendering = str(entry.get("zh") or "").strip()
        if not rendering:
            continue
        key, _, rest = str(entry.get("id", "")).partition(":")
        item = by_key.get(key)
        if item is None:
            reasons.append(f"{entry.get('id')}: no such item in this run")
            rejected += 1
            continue
        if rest == "title":
            ok = _accept(item.title, rendering, "title rendering")
            if ok and entry.get("text") == item.title:
                item.meta["title_zh"] = ok
                accepted += 1
                continue
        elif rest.startswith("quote:"):
            index = int(rest.split(":")[1])
            if index < len(item.evidence.quotes):
                quote = item.evidence.quotes[index]
                ok = _accept(quote.text, rendering, "quote rendering")
                if ok and entry.get("text") == quote.text:
                    quote.translation = ok
                    accepted += 1
                    continue
        rejected += 1
        reasons.append(f"{entry.get('id')}: rendering rejected "
                       f"(not Chinese, identical to source, evaluative, or text changed)")
    return accepted, rejected, reasons
