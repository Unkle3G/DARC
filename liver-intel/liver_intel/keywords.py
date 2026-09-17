"""Reader-facing keywords.

The article shows no line ids, no source adapter, no priority and no signal
names -- those are internal triage. What a reader gets is a row of keywords they
can search on, built from the same tagging the engine already did.
"""
from __future__ import annotations

from .domain_map import DomainMap
from .models import Item

#: Study tags rendered for a reader rather than an operator.
STUDY_TAG_ZH: dict[str, str] = {
    "PHASE1": "I期临床",
    "PHASE2": "II期临床",
    "PHASE3": "III期临床",
    "PHASE4": "IV期临床",
    "PRECLINICAL": "临床前",
    "TOPLINE": "顶线数据",
    "INTERIM": "期中分析",
    "ENDPOINT_MET": "达到主要终点",
    "ENDPOINT_MISSED": "未达主要终点",
    "BIOPSY_ENDPOINT": "肝活检终点",
    "SAFETY_SIGNAL": "安全性事件",
    "DILI_SIGNAL": "药物性肝损伤",
    "TERMINATED": "试验终止",
    "SUSPENDED": "试验暂停",
    "WITHDRAWN": "试验撤回",
    "ENROLMENT": "入组进展",
    "APPROVAL": "获批",
    "SUBMISSION": "申报受理",
    "CRL": "完整回复函",
    "DESIGNATION": "资格认定",
    "ADCOM": "咨询委员会",
    "LICENSING": "授权合作",
    "PUBLICATION": "论文发表",
    "GUIDELINE": "指南共识",
    "REAL_WORLD": "真实世界研究",
}

#: Short, searchable names for each line. Falls back to the dictionary's own
#: Chinese name when a line is not listed here.
LINE_KEYWORD_ZH: dict[str, str] = {
    "L1": "乙肝",
    "L2": "丁肝",
    "L3": "MASH",
    "L4": "胆汁淤积性肝病",
    "L5": "肝硬化",
    "L6": "肝细胞癌",
    "L7": "酒精性肝病",
    "L8": "肝移植",
    "L9": "丙肝",
    "L10": "戊肝",
    "L11": "遗传代谢性肝病",
    "L12": "自身免疫性肝炎",
    "L13": "人工智能",
    "L14": "无创诊断",
    "L15": "肠肝轴",
    "L16": "药物性肝损伤",
}


def reader_keywords(item: Item, dm: DomainMap, limit: int = 8) -> list[str]:
    """Keywords for the article body, ordered disease -> company -> drug -> study."""
    out: list[str] = []

    def add(value: str | None) -> None:
        value = (value or "").strip()
        if value and value not in out:
            out.append(value)

    for line_id in item.lines:
        keyword = LINE_KEYWORD_ZH.get(line_id)
        if keyword is None:
            line = dm.line(line_id)
            keyword = line.name_zh if line else line_id
        add(keyword)

    for company in (item.meta.get("companies") or [])[:2]:
        add(str(company))
    # A product's maker is searchable whether or not it is on the roster: a
    # registry record names its lead sponsor even when no roster company matched.
    add(str(item.meta.get("sponsor") or ""))
    for drug in (item.meta.get("drugs") or [])[:2]:
        add(str(drug))
    for tag in item.study:
        add(STUDY_TAG_ZH.get(tag))
    if item.meta.get("conference"):
        add(str(item.meta["conference"]))

    return out[:limit]
