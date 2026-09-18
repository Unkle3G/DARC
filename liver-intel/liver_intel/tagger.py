"""Three-layer tagger (rebuild of ``tagger_v1``).

Layer 1 -- disease lines  L1..L16      -> ``item.lines``
Layer 2 -- study/action tags          -> ``item.study``
Layer 3 -- entities (company, drug, KOL, region) -> ``item.meta``

Two behaviours carried over from v1 and called out in section 3 of the
handover:

* **L6 (HCC) disease-word gate** -- an oncology release only tags L6 when the
  text also carries liver context, so pan-tumour copy does not leak in.
* **L5 cirrhosis fallback** -- a bare cirrhosis / 肝硬化 mention is enough.

The infection and digestive lines were deliberately *not* built out; the
handover says they are unfinished and must not be extended here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .domain_map import Company, DomainMap, Kol, load_cached
from .models import Item
from .textutil import lead

# --- layer 2 vocabulary ---------------------------------------------------
# (tag, english patterns, chinese patterns).  English patterns are matched with
# word boundaries; Chinese by substring.
STUDY_PATTERNS: list[tuple[str, Sequence[str], Sequence[str]]] = [
    # Chinese Roman-numeral phases overlap as substrings (III\u671f contains I\u671f),
    # so each is anchored with look-arounds instead of plain containment.
    ("PHASE1", [r"phase\s*1\b", r"phase\s*i\b", r"first-in-human",
                r"(?<![IV])I\u671f(?![I\u671f])"], ["\u2160\u671f", "\u9996\u6b21\u4eba\u4f53"]),
    ("PHASE2", [r"phase\s*2\b", r"phase\s*ii\b", r"phase\s*2a\b", r"phase\s*2b\b",
                r"(?<!I)II\u671f(?!I)"], ["\u2161\u671f"]),
    ("PHASE3", [r"phase\s*3\b", r"phase\s*iii\b", r"pivotal trial", r"registrational trial",
                r"III\u671f"],
     ["\u2162\u671f", "\u5173\u952e\u6027\u8bd5\u9a8c", "\u6ce8\u518c\u4e34\u5e8a"]),
    ("PHASE4", [r"phase\s*4\b", r"phase\s*iv\b", r"post-marketing study", r"IV\u671f"],
     ["\u2163\u671f", "\u4e0a\u5e02\u540e\u7814\u7a76"]),
    ("PRECLINICAL", [r"preclinical", r"in vivo model", r"animal model"], ["临床前", "动物模型"]),
    ("TOPLINE", [r"topline", r"top-line", r"primary analysis", r"week \d+ results"],
     ["顶线", "主要分析", "主要结果"]),
    ("INTERIM", [r"interim analysis", r"interim results", r"dsmb", r"data safety monitoring"],
     ["期中分析", "中期分析", "数据安全监查"]),
    ("ENDPOINT_MET", [r"met the primary endpoint", r"achieved the primary endpoint",
                      r"statistically significant improvement", r"met both primary"],
     ["达到主要终点", "达到主要研究终点", "具有统计学显著"]),
    ("ENDPOINT_MISSED", [r"did not meet the primary endpoint", r"failed to meet the primary",
                         r"missed the primary endpoint", r"no statistically significant difference"],
     ["未达到主要终点", "未能达到主要终点", "无统计学显著"]),
    ("BIOPSY_ENDPOINT", [r"liver biopsy", r"biopsy-confirmed", r"fibrosis improvement",
                         r"nash resolution", r"mash resolution"],
     ["肝活检", "纤维化改善", "脂肪性肝炎缓解"]),
    ("SAFETY_SIGNAL", [r"serious adverse event", r"treatment-related death", r"black box",
                       r"boxed warning", r"safety signal"],
     ["严重不良事件", "死亡病例", "黑框警告", "安全性信号"]),
    ("DILI_SIGNAL", [r"drug-induced liver injury", r"hy's law", r"alt elevation",
                     r"transaminase elevation", r"hepatotoxicity"],
     ["药物性肝损伤", "转氨酶升高", "肝毒性", "海氏法则"]),
    ("TERMINATED", [r"\bterminat(?:ed|ion)\b", r"discontinu(?:ed|ation) of the (?:study|trial|program)"],
     ["终止", "中止"]),
    ("SUSPENDED", [r"\bsuspended\b", r"clinical hold"], ["暂停", "临床暂停"]),
    ("WITHDRAWN", [r"\bwithdrawn\b"], ["撤回", "撤销"]),
    ("ENROLMENT", [r"first patient (?:dosed|enrolled)", r"completed enrol(?:l)?ment",
                   r"fully enrolled"],
     ["首例受试者", "完成入组", "入组完成"]),
    ("APPROVAL", [r"\bapproved\b", r"marketing authorisation", r"marketing authorization",
                  r"\bnda approval\b", r"granted approval",
                  r"\b(?:fda|ema|nmpa|chmp|pmda|mhra)\s+approval\b"],
     ["获批", "批准上市", "上市许可", "批准注册"]),
    # A bare application id ("IND 152626") is a *mention*, and mentions appear in
    # withdrawals as often as in filings -- a real registry entry read
    # "requests the permanent discontinuation and withdrawal of Investigational
    # New Drug application IND 152626" and the bare-id pattern reported it as a
    # filing. So the act has to be present, and WITHDRAWAL_CONTEXT below vetoes
    # the tag when the same sentence is about taking an application back.
    ("SUBMISSION", [r"(?:submit\w*|filed?|filing|accept\w*)\s+(?:\w+\s+){0,4}"
                    r"(?:\bnda\b|\bbla\b|\bmaa\b|\bsnda\b|\bind\b|application)",
                    r"(?:\bnda\b|\bbla\b|\bmaa\b|\bsnda\b|\bind\b)\s+(?:\w+\s+){0,3}"
                    r"(?:was |has been )?(?:submitted|filed|accepted)",
                    r"regulatory submission", r"marketing application"],
     ["上市申请", "新药申请", "受理", "申报", "临床试验申请"]),
    # Every identifier here is word-bounded on purpose: an unbounded `ind`
    # matches inside "indication", and a label reading "this indication is
    # approved under accelerated approval" was read as a cleared trial
    # application. Bare tokens have inverted a signal's meaning three times now.
    ("TRIAL_CLEARANCE",
     [r"(?:\bapplication\b|\bind\b|\bcta\b)[^.]{0,80}for\s+(?:a\s+)?clinical trial"
      r"[^.]{0,60}(?:approved|cleared|accepted)",
      r"clinical trial (?:application|authorisation|authorization)[^.]{0,60}"
      r"(?:approved|cleared|granted|accepted)",
      r"(?:\bind\b|investigational new drug)[^.]{0,40}"
      r"(?:cleared|approved|allowed to proceed)",
      r"may proceed letter"],
     ["临床试验申请获批", "临床试验申请获得批准", "临床试验批件", "获准开展临床试验", "默示许可"]),
    ("CRL", [r"complete response letter", r"\bcrl\b", r"refuse to file"], ["完整回复函"]),
    ("DESIGNATION", [r"breakthrough therapy", r"fast track", r"orphan drug", r"priority review",
                     r"prime designation", r"regenerative medicine advanced therapy"],
     ["突破性治疗", "优先审评", "孤儿药", "附条件批准"]),
    ("ADCOM", [r"advisory committee", r"\badcom\b", r"chmp opinion", r"chmp adopted"],
     ["专家咨询委员会", "CHMP"]),
    ("LICENSING", [r"licensing agreement", r"license agreement", r"out-licens", r"in-licens",
                   r"collaboration agreement", r"acquisition of", r"definitive agreement",
                   r"exclusive rights"],
     ["授权许可", "对外授权", "战略合作", "收购", "独家权益"]),
    ("PUBLICATION", [r"published in", r"peer-reviewed", r"new england journal", r"the lancet",
                     r"journal of hepatology", r"\bhepatology\b"],
     ["发表于", "同行评审"]),
    # A society states its own work in more ways than one: the Baveno VIII
    # output was announced as a "Consensus Conference" issuing "updated
    # guidance" and "clinical recommendations", and matched none of the
    # original three phrases.
    ("GUIDELINE", [r"clinical practice guideline", r"practice guidance",
                   r"consensus (?:statement|conference|document|report)",
                   r"clinical recommendations", r"updated guidance",
                   r"guidance on\s+\w+", r"position (?:paper|statement)",
                   r"guidance for industry", r"guideline update"],
     ["临床指南", "专家共识", "诊疗规范", "指南更新", "共识会议", "诊疗指南"]),
    ("REAL_WORLD", [r"real-world evidence", r"real-world data", r"registry cohort"],
     ["真实世界"]),
]

#: A sentence about taking an application back must not read as a filing.
WITHDRAWAL_CONTEXT = re.compile(
    r"(?i)\b(?:withdraw\w*|discontinu\w*|terminat\w*|rescind\w*|revoke\w*)\b"
    r"|撤回|撤销|终止")

#: Phrases that show up in drug naming; used to lift a compound name out of a
#: headline when the release does not carry structured metadata.
#: A space-separated code needs three digits ("MK 3475"); two letters and two
#: digits with a space between them is a fragment of prose ("EN 09" came out of
#: a filing and went into the reader's keyword row as a compound).
_DRUG_CODE = re.compile(r"\b([A-Z]{2,5}(?:-?\d{2,5}|\s\d{3,5})[A-Za-z]?)\b")
_NOT_A_CODE = {"EN", "ON", "IN", "AT", "NO", "OF", "TO", "BY", "OR", "AN", "AS", "IS",
               "US", "UK", "EU", "ET", "AM", "PM", "MG", "ML", "KG", "CI", "HR", "OR"}
_INN_SUFFIX = re.compile(
    r"\b([a-z][a-z\-]{4,}(?:tide|mab|nib|stat|siran|vir|prazole|fexor|glitazar|"
    r"delpar|branor|rasib|ciclib|zumab|ximab|umab))\b", re.I)


#: A statement about something that has not happened yet is not an event.
FUTURE_TENSE = re.compile(
    r"(?i)\b(?:expected|expects?|anticipat\w+|plans? to|planned|will\s+\w+|"
    # "guidance" alone is not a future marker: to a society it means clinical
    # guidance, and treating it as one silently suppressed a consensus
    # statement. Only the financial sense forecasts anything.
    r"on track|upcoming|projected|targeting|intends? to|"
    r"(?:financial|revenue|earnings|fiscal|full[- ]year)\s+guidance|"
    r"to be (?:reported|presented|initiated|completed)|later this year|"
    r"in the (?:first|second|third|fourth) quarter of \d{4})\b"
    r"|预计|计划|拟于|将于|有望")


@dataclass
class SentenceTags:
    """Study tags carried by one sentence, and whether it is about the future."""

    text: str
    tags: set[str]
    future: bool

    def to_json(self) -> dict[str, object]:
        return {"text": self.text, "tags": sorted(self.tags), "future": self.future}


@dataclass
class TagResult:
    lines: list[str] = field(default_factory=list)
    study: list[str] = field(default_factory=list)
    companies: list[str] = field(default_factory=list)
    tickers: list[str] = field(default_factory=list)
    drugs: list[str] = field(default_factory=list)
    kols: list[str] = field(default_factory=list)
    region: str = ""
    gated_out: list[str] = field(default_factory=list)
    company_tier: int | None = None


def _compile(patterns: Iterable[str]) -> re.Pattern[str]:
    joined = "|".join(f"(?:{p})" for p in patterns)
    return re.compile(joined, re.I)


#: A short all-caps alias is also a gene symbol waiting to happen. ``GSK`` is
#: the company; ``GSK 3Β`` and ``GSK-3β`` in a mechanism abstract are glycogen
#: synthase kinase 3, and the word boundary alone cannot tell them apart -- a
#: phytochemical review that merely named the kinase was filed under GSK and
#: printed "出处：GSK". A numbered suffix is what makes it a symbol, so short
#: aliases refuse to match in front of one.
_SYMBOL_SUFFIX = r"(?!\s*[-‐-―]?\s*\d)"


def _term_pattern(terms: Iterable[str], prefix: bool = False,
                  symbol_guard: bool = False) -> re.Pattern[str] | None:
    """Word-bounded for ASCII terms, plain containment for CJK.

    ``prefix=True`` drops the trailing boundary so a gate word like ``hepat``
    covers hepatic / hepatology / hepatocellular.  ``symbol_guard=True`` keeps a
    short all-caps term from matching a numbered gene symbol (see
    ``_SYMBOL_SUFFIX``).
    """
    parts = []
    for term in terms:
        term = term.strip()
        if not term:
            continue
        escaped = re.escape(term).replace(r"\ ", r"\s+")
        if re.match(r"^[\x00-\x7f]+$", term):
            tail = "" if prefix else r"(?![A-Za-z0-9])"
            if symbol_guard and len(term) <= 4 and term.isupper() and term.isalpha():
                tail += _SYMBOL_SUFFIX
            parts.append(rf"(?<![A-Za-z0-9]){escaped}{tail}")
        else:
            parts.append(escaped)
    if not parts:
        return None
    return re.compile("|".join(parts), re.I)


class Tagger:
    def __init__(self, domain_map: DomainMap | None = None,
                 use_unverified_kols: bool = False):
        self.dm = domain_map or load_cached()
        self.use_unverified_kols = use_unverified_kols
        self._line_terms: dict[str, re.Pattern[str] | None] = {}
        self._line_gated_terms: dict[str, re.Pattern[str] | None] = {}
        self._line_gates: dict[str, re.Pattern[str] | None] = {}
        for line_id, line in self.dm.lines.items():
            terms = list(line.terms_en) + list(line.terms_zh) + list(line.fallback_terms_zh)
            self._line_terms[line_id] = _term_pattern(terms)
            self._line_gated_terms[line_id] = _term_pattern(
                list(line.gated_terms_en) + list(line.gated_terms_zh))
            gate_terms = list(line.gate_en) + list(line.gate_zh)
            # Gate words match as prefixes ("hepat" -> "hepatology", "hepatic").
            self._line_gates[line_id] = (
                _term_pattern(gate_terms, prefix=True) if gate_terms else None)
        self._study = [(tag, _compile(list(en) + [re.escape(z) for z in zh]))
                       for tag, en, zh in STUDY_PATTERNS]
        self._companies: list[tuple[Company, re.Pattern[str]]] = []
        for company in self.dm.companies:
            pattern = _term_pattern(company.aliases, symbol_guard=True)
            if pattern is not None:
                self._companies.append((company, pattern))
        self._kols: list[tuple[Kol, re.Pattern[str]]] = []
        for kol in self.dm.kols:
            if not kol.verified and not self.use_unverified_kols:
                continue
            pattern = _term_pattern([kol.name])
            if pattern is not None:
                self._kols.append((kol, pattern))

    # -- layer 1 ---------------------------------------------------------
    def tag_lines(self, text: str) -> tuple[list[str], list[str]]:
        """Return (fired lines, lines that matched only an ambiguous term and
        failed their disease-word gate).

        An unambiguous term (``terms_en`` / ``terms_zh``) fires the line on its
        own.  An ambiguous one (``gated_terms_*`` -- "HCC", "TACE", "BCLC")
        needs a gate word somewhere in the text, which is what keeps pan-tumour
        copy that name-drops HCC out of L6.
        """
        hits: list[str] = []
        gated: list[str] = []
        for line_id in self.dm.lines:
            strong = self._line_terms.get(line_id)
            if strong is not None and strong.search(text):
                hits.append(line_id)
                continue
            weak = self._line_gated_terms.get(line_id)
            if weak is None or not weak.search(text):
                continue
            gate = self._line_gates.get(line_id)
            if gate is None or gate.search(text):
                hits.append(line_id)
            else:
                gated.append(line_id)
        hits.sort(key=lambda lid: int(lid[1:]))
        return hits, gated

    # -- layer 2 ---------------------------------------------------------
    def tag_study(self, text: str) -> list[str]:
        tags = []
        for tag, pattern in self._study:
            match = pattern.search(text)
            if not match:
                continue
            if tag == "SUBMISSION" and self._is_withdrawal(text, match.start()):
                continue
            tags.append(tag)
        # An explicit endpoint verdict makes the generic TOPLINE tag redundant
        # only when it disagrees with nothing; keep both, they carry different
        # information for the grader.
        return tags

    @staticmethod
    def _is_withdrawal(text: str, position: int) -> bool:
        """True when the sentence around ``position`` is about withdrawing."""
        start = max(0, text.rfind(".", 0, position) + 1)
        end = text.find(".", position)
        sentence = text[start: end if end != -1 else len(text)]
        return bool(WITHDRAWAL_CONTEXT.search(sentence))

    def sentence_tags(self, text: str) -> list[SentenceTags]:
        """Per-sentence study tags.

        Signals are assembled per sentence rather than per document: one release
        read "Initiated global PERFORMA Phase 3 trial" in one line and "Reported
        positive topline data from RECLAIM Phase 2 trial" in the next, and
        pooling the document's tags turned that into a Phase 3 readout nobody
        announced.
        """
        out: list[SentenceTags] = []
        for sentence in _split_sentences(text or ""):
            tags = {tag for tag, pattern in self._study if pattern.search(sentence)}
            if "SUBMISSION" in tags and WITHDRAWAL_CONTEXT.search(sentence):
                tags.discard("SUBMISSION")
            if tags:
                out.append(SentenceTags(sentence, tags,
                                        bool(FUTURE_TENSE.search(sentence))))
        return out

    # -- layer 3 ---------------------------------------------------------
    def tag_entities(self, text: str) -> tuple[list[Company], list[str], list[Kol]]:
        companies = [c for c, pattern in self._companies if pattern.search(text)]
        kols = [k for k, pattern in self._kols if pattern.search(text)]
        drugs = self._drugs(text)
        return companies, drugs, kols

    @staticmethod
    def _drugs(text: str) -> list[str]:
        found: list[str] = []
        for match in _DRUG_CODE.finditer(text):
            token = match.group(1)
            if token.upper().startswith(("NCT", "EX-", "ISO", "COVID")):
                continue
            if re.match(r"[A-Z]+", token).group(0) in _NOT_A_CODE:
                continue
            found.append(token)
        for match in _INN_SUFFIX.finditer(text):
            found.append(match.group(1).lower())
        seen: set[str] = set()
        out = []
        for drug in found:
            key = drug.lower().replace(" ", "-")
            if key in seen:
                continue
            seen.add(key)
            out.append(drug)
        return out[:8]

    # -- driver ----------------------------------------------------------
    def tag_text(self, text: str) -> TagResult:
        text = text or ""
        lines, gated = self.tag_lines(text)
        study = self.tag_study(text)
        companies, drugs, kols = self.tag_entities(text)
        tier = min((c.tier for c in companies), default=None)
        region = ""
        if companies:
            markets = {c.market for c in companies}
            region = "cn" if markets <= {"cn", "hk"} else sorted(markets)[0]
        return TagResult(
            lines=lines, study=study,
            companies=[c.name for c in companies],
            tickers=[c.ticker for c in companies if c.ticker],
            drugs=drugs,
            kols=[k.name for k in kols],
            region=region, gated_out=gated, company_tier=tier,
        )

    def study_evidence(self, text: str, tags: Iterable[str] | None = None
                       ) -> list[tuple[str, str]]:
        """(tag, sentence) pairs quoting where each study tag matched.

        This is what lets a rules-only run still satisfy "命中信号 + 原文依据":
        the sentence is copied out of the source verbatim, so it survives the
        same quote check the LLM output goes through.
        """
        wanted = set(tags) if tags is not None else None
        sentences = _split_sentences(text or "")
        out: list[tuple[str, str]] = []
        for tag, pattern in self._study:
            if wanted is not None and tag not in wanted:
                continue
            for sentence in sentences:
                if pattern.search(sentence):
                    out.append((tag, sentence))
                    break
        return out

    def apply(self, item: Item, extra_text: str = "") -> Item:
        """Tag an item in place from its title, body and metadata.

        Lines are read from the whole document -- what a release is *about* can
        be established anywhere in it. Study tags are read from the lead only,
        because what a release *announces* is stated at the top: a quarterly
        report that recaps a past readout in a bullet on page three has not
        announced that readout today, and grading must not treat it as though
        it had.
        """
        body = item.meta.get("body", "") or ""
        blob = "\n".join(filter(None, [item.title, body, extra_text,
                                       str(item.meta.get("summary", ""))]))
        head = item.meta.get("lead")
        if head is None:
            head = lead(body) if body else ""
            item.meta["lead"] = head
        headline_blob = "\n".join(filter(None, [item.title, head,
                                                str(item.meta.get("summary", ""))]))

        result = self.tag_text(blob)
        sentences = self.sentence_tags(headline_blob)
        item.meta["sentence_tags"] = [st.to_json() for st in sentences]
        announced = sorted({tag for st in sentences for tag in st.tags})
        mentioned = sorted(set(result.study) - set(announced))

        item.lines = result.lines
        item.study = sorted(set(item.study) | set(announced))
        if mentioned:
            # Kept for the internal report: stated somewhere in the document,
            # but not what it announces.
            item.meta["study_mentioned"] = mentioned
        meta = item.meta
        if result.companies:
            meta["companies"] = result.companies
        if result.tickers:
            meta["tickers"] = result.tickers
        if result.drugs:
            meta["drugs"] = result.drugs
        if result.kols:
            meta["kols"] = result.kols
        if result.region and "region" not in meta:
            meta["region"] = result.region
        if result.gated_out:
            meta["lines_gated_out"] = result.gated_out
        if result.company_tier is not None:
            meta["company_tier"] = result.company_tier
        return item


_SENTENCE_END = re.compile(r"(?<=[.!?\u3002\uff01\uff1f])\s+|\n+")


def _split_sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE_END.split(text or "")]
    return [part for part in parts if len(part) > 12]
