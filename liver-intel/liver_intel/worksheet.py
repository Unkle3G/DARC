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

from .models import Item, Quote, _normalise_ws
from .translate import (SUMMARY_LIMIT, _accept, accept_summary, needs_rendering,
                        quote_needs_rendering)

#: What the judgement worksheet asks for. Same contract as the API step in
#: ``llm.py``: which signals the document *states*, and the sentence that states
#: each one -- never a conclusion, never outside knowledge.
JUDGEMENT_RULES = (
    "你是抽取步骤，不是分析师：只回答「这份文档说了哪些信号」和「哪句话这么说的」。",
    "signal 只能取 signals 清单里的名字，清单外的一律丢弃。",
    "每个信号必须配至少一条 quote，quote 的 text 必须逐字出现在该条目的 source_text 中；"
    "引擎会核对，对不上的整条丢弃。宁可少报，不可编造。",
    "文档没有明确说出来的信号就不要报。空列表是常见且正确的结果。",
    "不预测、不评价、不排序，不写「利好」「有望」「值得关注」这类措辞。",
    "zh 是该 quote 的中文译文；中文原文留空。药名、代号、试验名、公司名、分期写法保持原文。",
    "不要改动 item、title、source_text 或 rule_signals 字段。",
)

RULES = (
    "把每个 text 逐字、完整地译成简体中文，填入 zh。",
    "药名、药物代号、化合物名、试验名称、公司名、基因/靶点名、注册号、期刊名一律保持原文写法，"
    "不译、不换成中文通用名（cemiplimab 写 cemiplimab，不写西米普利单抗）。",
    "符号与记法与原文一致：+/- 就写 +/-，不改成 ±；大小写、连字符照原文。",
    "试验分期用原文写法：Phase 3 写 Phase 3，PHASE3 写 PHASE3，不写 III期；注册库的 phases 字段值不译。",
    "只译原文写了的内容：不加背景、不加解释、不加术语注释、不加括号说明。",
    "不预测、不评价、不排序，不使用利好/利空/有望/重磅之类的措辞。",
    "拿不准的留空（zh 保持 \"\"），引擎会让原文单独呈现；宁缺毋滥。",
    "不要改动 text、id 或其他字段。",
    "需要向读者交代'这是什么'时，不要写注释，而是在该条目的 supplementary 里补一条引文："
    "text 必须是原文文档（source_text）中逐字存在的一句话，zh 是它的译文。引擎会核对 text "
    "确实出现在原文中，对不上的整条丢弃。",
)

#: What the opening-summary slot asks for. Same posture as everywhere else:
#: restate what the issue carries, add nothing to it.
SUMMARY_RULES = (
    f"用中文写一段导读，放在详细条目之前，不超过 {SUMMARY_LIMIT} 字（按非空白字符计）。",
    "只复述本期条目里已有的内容：写清今天有什么、分别来自哪里，不补背景、不加解释。",
    "不预测、不评价、不排序，不写「利好」「有望」「值得关注」「重磅」这类措辞。",
    "不要带入本期材料里没有的数字。引擎会核对，出现新数字的整段丢弃。",
    "药名、代号、试验名、公司名、期刊名、分期写法保持原文。",
    "一段连续文字，不用列表、不用小标题。拿不准就留空，留空则不印这一段。",
)

#: How much of the source document travels with the worksheet, so a
#: supplementary quote can be chosen from it without opening anything else.
SOURCE_EXCERPT_CHARS = 6000


def _slots(item: Item) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if needs_rendering(item.title) and not item.meta.get("title_zh"):
        out.append({"id": f"{item.key}:title", "kind": "title", "text": item.title, "zh": ""})
    for index, quote in enumerate(item.evidence.quotes):
        if quote_needs_rendering(quote) and not quote.translation:
            kind = "field" if quote.locator.startswith("ClinicalTrials.gov") else "quote"
            out.append({"id": f"{item.key}:quote:{index}", "kind": kind,
                        "locator": quote.locator, "text": quote.text, "zh": ""})
    return out


def _source_text(item: Item) -> str:
    return str(item.meta.get("quotable") or item.meta.get("body") or "")


def write_judgement(items: list[Item], path: Path, report_date: str,
                    signals: dict[str, dict] | None = None) -> int:
    """Hand the session the candidates to judge; return how many.

    This is the significance step of section 0 run by the operator's own Claude
    session instead of an API call. It sits *before* selection, because what it
    finds changes the grade, which changes what ships -- so the candidates are
    everything that matched a disease line, not just what the rules already
    chose.
    """
    from .grade import SIGNALS

    catalogue = {name: str(spec["desc"]) for name, spec in sorted(
        (signals or SIGNALS).items())}
    entries = [{
        "item": item.key,
        "title": item.title,
        "src": item.src,
        "url": item.url,
        "rule_signals": list(item.evidence.signals),
        "source_text": _source_text(item)[:SOURCE_EXCERPT_CHARS],
        "signals": [],
        "quotes": [],
    } for item in items]
    path.write_text(json.dumps(
        {"date": report_date, "rules": list(JUDGEMENT_RULES), "signals": catalogue,
         "entries": entries}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(entries)


def apply_judgement(items: list[Item], path: Path) -> tuple[int, int, list[str]]:
    """Read a filled judgement worksheet onto the items.

    A signal outside the vocabulary is dropped; a quote that is not verbatim in
    the item's own source text is dropped; a signal left with no surviving quote
    is dropped. Those are the same three guards the API step applies -- "命中信号
    + 原文依据" means a signal without its evidence is not a finding.
    """
    from .grade import SIGNALS

    payload = json.loads(path.read_text(encoding="utf-8"))
    by_key = {item.key: item for item in items}
    applied = rejected = 0
    reasons: list[str] = []
    for entry in payload.get("entries", []):
        item = by_key.get(str(entry.get("item", "")))
        if item is None:
            continue
        haystack = _normalise_ws(_source_text(item))
        quotes_by_signal: dict[str, list[Quote]] = {}
        for raw in entry.get("quotes") or []:
            signal = str(raw.get("signal") or "").strip()
            text = str(raw.get("text") or "").strip()
            if not text:
                continue
            if signal not in SIGNALS:
                rejected += 1
                reasons.append(f"{item.key[:8]} 判定：信号 {signal!r} 不在词表内，丢弃")
                continue
            if _normalise_ws(text) not in haystack:
                rejected += 1
                reasons.append(f"{item.key[:8]} 判定：引文未在原文中逐字出现，丢弃："
                               f"{text[:50]!r}")
                continue
            rendering = _accept(text, str(raw.get("zh") or ""), "judgement rendering") \
                if needs_rendering(text) else ""
            quotes_by_signal.setdefault(signal, []).append(
                Quote(text=text, url=item.url, locator=f"判定:{signal}",
                      translation=rendering))
        for signal in entry.get("signals") or []:
            signal = str(signal).strip()
            if signal not in SIGNALS:
                rejected += 1
                reasons.append(f"{item.key[:8]} 判定：信号 {signal!r} 不在词表内，丢弃")
                continue
            if signal not in quotes_by_signal:
                rejected += 1
                reasons.append(f"{item.key[:8]} 判定：{signal} 没有可核对的原文依据，丢弃")
                continue
        already = {q.text.strip() for q in item.evidence.quotes}
        for signal, quotes in quotes_by_signal.items():
            if signal not in item.evidence.signals:
                item.evidence.signals = sorted(set(item.evidence.signals) | {signal})
            for quote in quotes:
                if quote.text.strip() not in already:
                    item.evidence.quotes.append(quote)
                    already.add(quote.text.strip())
            applied += 1
    return applied, rejected, reasons


def write(items: list[Item], path: Path, report_date: str,
          summary: str = "") -> int:
    """Write the worksheet; return how many rendering slots it holds.

    Besides the rendering slots, every item gets a ``supplementary`` list (empty)
    and an excerpt of its source text: a quote added there must be a verbatim
    sentence of that text, which is how "explain what this is" stays inside
    "quote the source".

    The ``summary`` block is the day's opening paragraph. It carries a digest of
    what shipped -- priority, source, signals, both titles -- so the paragraph
    can be written from the worksheet alone. ``summary`` pre-fills it, for a
    reissue whose selection did not change; an empty slot prints no paragraph.
    """
    entries = []
    documents = []
    digest = []
    for item in items:
        for slot in _slots(item):
            slot["item_title"] = item.title
            entries.append(slot)
        documents.append({
            "item": item.key, "item_title": item.title,
            "source_text": _source_text(item)[:SOURCE_EXCERPT_CHARS],
            "supplementary": [],
        })
        digest.append({
            "P": item.P, "src": item.src,
            "journal": str(item.meta.get("journal") or ""),
            "title": item.title, "title_zh": str(item.meta.get("title_zh") or ""),
            "signals": list(item.evidence.signals),
        })
    payload = {"date": report_date, "rules": list(RULES), "entries": entries,
               "documents": documents,
               "summary": {"rules": list(SUMMARY_RULES), "limit": SUMMARY_LIMIT,
                           "items": digest, "zh": summary}}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(entries)


def _allowed_numbers(items: list[Item]) -> list[str]:
    """Every number the day's own material carries.

    A summary may restate these and nothing else. Item counts are included
    because "本期 7 条" is a fact about the issue the engine itself produced.
    """
    from .translate import _NUMBERS

    blob = []
    for item in items:
        blob.append(item.title)
        blob.append(str(item.meta.get("title_zh") or ""))
        blob.append(_source_text(item))
        for quote in item.evidence.quotes:
            blob.append(quote.text)
            blob.append(quote.translation or "")
    numbers = _NUMBERS.findall("\n".join(blob))
    counts = [str(len(items))]
    for priority in ("P0", "P1", "P2", "P3"):
        counts.append(str(sum(1 for item in items if item.P == priority)))
    return numbers + counts


def apply_summary(items: list[Item], path: Path) -> tuple[str, str]:
    """Read the worksheet's opening summary. Returns (summary, reason-if-dropped)."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    block = payload.get("summary") or {}
    limit = int(block.get("limit") or SUMMARY_LIMIT)
    return accept_summary(str(block.get("zh") or ""), _allowed_numbers(items),
                          limit=limit, where="导读")


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

    for document in payload.get("documents", []):
        item = by_key.get(str(document.get("item", "")))
        if item is None:
            continue
        haystack = _normalise_ws(_source_text(item))
        already = {q.text.strip() for q in item.evidence.quotes}
        for extra in document.get("supplementary") or []:
            text = str(extra.get("text") or "").strip()
            if not text:
                continue
            if _normalise_ws(text) not in haystack:
                rejected += 1
                reasons.append(f"{item.key[:8]} supplementary: not found verbatim in the "
                               f"source -- dropped: {text[:60]!r}")
                continue
            if text in already:
                continue
            rendering = _accept(text, str(extra.get("zh") or ""), "supplementary rendering") \
                if needs_rendering(text) else ""
            item.evidence.quotes.append(
                Quote(text=text, url=item.url, locator="supplementary", translation=rendering))
            already.add(text)
            accepted += 1
    return accepted, rejected, reasons
