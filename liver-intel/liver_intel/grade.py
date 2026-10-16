"""Priority grading (task sheet section 0).

P0 is reserved for materially significant events.  The decision is expressed as
*signals*: a deterministic rule fires a signal, the LLM step may fire the same
signals from prose, and either way the item must carry the source text behind
it.  Nothing here writes an opinion -- ``why`` is a factual join of the signals
that fired.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from .config import MAIN_LINES
from .domain_map import DomainMap, load_cached
from .journals import tier_of
from .models import Item
from .tagger import is_correspondence

#: The closed signal vocabulary.  ``hard`` signals are P0 on their own; ``soft``
#: signals are P1.  The LLM step may only emit ids from this table.
SIGNALS: dict[str, dict[str, object]] = {
    # --- hard (P0) ---
    "PH3_RESULT":        {"weight": "hard", "desc": "Phase 3 / pivotal efficacy readout"},
    "PH3_STOPPED":       {"weight": "hard", "desc": "Phase 3 terminated, suspended or withdrawn with a stated reason"},
    "REG_APPROVAL":      {"weight": "hard", "desc": "Regulatory approval or marketing authorisation"},
    "REG_REJECTION":     {"weight": "hard", "desc": "Complete response letter / refusal / negative opinion"},
    "CLINICAL_HOLD":     {"weight": "hard", "desc": "Clinical hold imposed or lifted"},
    "SAFETY_SERIOUS":    {"weight": "hard", "desc": "Death, Hy's law case or boxed warning"},
    "MARKET_WITHDRAWAL": {"weight": "hard", "desc": "Product withdrawn from market"},
    "COMPANY_CONTROL":   {"weight": "hard", "desc": "Acquisition of, or merger involving, a roster company"},
    # --- soft (P1) ---
    "PH2_RESULT":        {"weight": "soft", "desc": "Phase 2 efficacy readout"},
    "REG_SUBMISSION":    {"weight": "soft", "desc": "NDA/BLA/MAA filed or accepted for review"},
    "REG_DESIGNATION":   {"weight": "soft", "desc": "Breakthrough, priority review, orphan or conditional designation"},
    "REG_MILESTONE":     {"weight": "soft", "desc": "CHMP opinion, advisory committee date, PDUFA date"},
    "PH3_START":         {"weight": "soft", "desc": "Phase 3 initiated or first patient dosed"},
    "DEAL":              {"weight": "soft", "desc": "Licensing, collaboration or asset transaction"},
    "PIVOTAL_PUBLICATION": {"weight": "soft", "desc": "Pivotal data published in a peer-reviewed journal"},
    "JOURNAL_PIVOTAL":   {"weight": "soft",
                          "desc": "Clinical result published in a roster tier-A journal"},
    "GUIDELINE":         {"weight": "soft", "desc": "Society guideline or consensus statement"},
    "SAFETY_WATCH":      {"weight": "soft", "desc": "Hepatotoxicity or DILI signal short of a serious case"},
    # --- background (P2) ---
    "EARLY_RESULT":      {"weight": "background", "desc": "Phase 1 / preclinical / translational result"},
    "TRIAL_PROGRESS":    {"weight": "background", "desc": "Enrolment, protocol or registry progress"},
    "TRIAL_STOPPED":     {"weight": "background",
                          "desc": "Trial other than Phase 3 terminated, suspended or withdrawn with a stated reason"},
    "REG_LIST_CHANGE":   {"weight": "background", "desc": "Regulator list page changed (acceptance, review queue)"},
    "REG_TRIAL_CLEARANCE": {"weight": "background",
                            "desc": "Clinical trial application (IND/CTA) cleared -- permission to start a trial, not a marketing approval"},
    "JOURNAL_MAJOR":     {"weight": "background",
                          "desc": "Roster journal publication without a clinical result"},
    "CORPORATE":         {"weight": "background", "desc": "Financing, personnel or other corporate item"},
}

HARD_SIGNALS = {k for k, v in SIGNALS.items() if v["weight"] == "hard"}
SOFT_SIGNALS = {k for k, v in SIGNALS.items() if v["weight"] == "soft"}
BACKGROUND_SIGNALS = {k for k, v in SIGNALS.items() if v["weight"] == "background"}

BASE_SCORE = {"P0": 10.0, "P1": 6.0, "P2": 3.0, "P3": 1.0}

#: A quarterly or annual report recaps a period; the events in its business
#: update already happened and were announced on their own. Reading one as if it
#: broke that news double-counts it -- the same Phase 2 readout showed up once as
#: its own release and again in the quarter's summary two weeks later.
_PERIODIC_TITLE = re.compile(
    r"(?i)\b(?:first|second|third|fourth|1st|2nd|3rd|4th|full[- ]year|fiscal)\s+"
    r"(?:quarter|half|year)\b|\bq[1-4]\s+\d{4}\b|\b(?:quarterly|annual) results\b"
    r"|\bfinancial results\b|\bresults of operations\b|业绩|季度报告|年度报告")


def is_periodic_report(item: Item) -> bool:
    """True when the document is a periodic financial report."""
    if _PERIODIC_TITLE.search(item.title or ""):
        return True
    edgar_items = item.meta.get("edgar_items") or []
    return edgar_items == ["2.02"]


@dataclass
class GradeResult:
    P: str
    signals: list[str] = field(default_factory=list)
    why: str = ""
    score: float = 0.0
    main_line_hit: bool = False
    notes: list[str] = field(default_factory=list)


def rule_signals(item: Item) -> list[str]:
    """Signals derivable from layer-2 tags without any model call.

    A signal is a claim about one event, so it is assembled from the tags of a
    single statement. Two facts in two different sentences -- a Phase 3 trial
    started, a Phase 2 trial read out -- are not a Phase 3 readout, and a
    sentence in the future tense is not an event at all.

    ``meta.sentence_tags`` carries those per-sentence sets. Adapters that state
    their facts structurally rather than in prose (the trial registry) put theirs
    in ``meta.structural_study``, and that group is read the same way.
    """
    sentences = item.meta.get("sentence_tags")
    structural = set(item.meta.get("structural_study") or [])
    groups: list[set[str]] = []
    if structural:
        # A registry record states everything in fields; its "sentences" are
        # scaffolding the adapter wrote ("Phase: PHASE3"), and reading those as
        # statements made every terminated trial also report "trial progress".
        groups.append(structural)
    else:
        for entry in sentences or []:
            if entry.get("future"):
                continue
            tags = set(entry.get("tags") or [])
            if tags:
                groups.append(tags)
    if not groups and sentences is None and not structural:
        # No per-statement breakdown was produced at all: fall back to the
        # document's tags. Note the condition -- once a breakdown exists, an
        # empty result means every statement was in the future tense, and
        # falling back would resurrect exactly the event the tense ruled out.
        groups = [set(item.study)]

    out: list[str] = []
    for study in groups:
        for signal in _signals_for(study, item):
            if signal not in out:
                out.append(signal)
    for signal in _journal_signals(item, {tag for group in groups for tag in group}):
        if signal not in out:
            out.append(signal)
    return out


#: What makes a paper a *result* rather than a review, an editorial or a
#: mechanism study. The roster decides which journals count; this decides
#: whether the paper reports something clinical.
CLINICAL_EVIDENCE = {"TOPLINE", "INTERIM", "ENDPOINT_MET", "ENDPOINT_MISSED",
                     "BIOPSY_ENDPOINT", "PHASE2", "PHASE3", "PHASE4", "REAL_WORLD",
                     "SAFETY_SIGNAL", "DILI_SIGNAL"}


def _journal_signals(item: Item, study: set[str]) -> list[str]:
    """Weight a paper by where it appeared and whether it reports a result.

    PubMed indexes everything, so "published" says nothing on its own: before
    the roster existed, every article fired PUBLICATION alone, which is no
    signal, and the literature never left P3. A journal off the roster still
    collects and still reaches the weekly digest -- it just does not compete
    for a daily slot.
    """
    if item.meta.get("src_kind") != "journal":
        return []
    # Correspondence is not a result, so where it appeared carries no weight.
    if is_correspondence(item.meta.get("publication_types")):
        return []
    tier = tier_of(str(item.meta.get("journal") or ""))
    if not tier:
        return []
    item.meta["journal_tier"] = tier
    clinical = bool(study & CLINICAL_EVIDENCE)
    if tier == "A":
        return ["JOURNAL_PIVOTAL"] if clinical else ["JOURNAL_MAJOR"]
    return ["JOURNAL_MAJOR"] if clinical else []


def _signals_for(study: set[str], item: Item) -> list[str]:
    out: list[str] = []

    phase3 = "PHASE3" in study
    phase2 = "PHASE2" in study
    verdict = study & {"ENDPOINT_MET", "ENDPOINT_MISSED"}
    readout = bool(verdict) or "TOPLINE" in study or "INTERIM" in study

    stopped = study & {"TERMINATED", "SUSPENDED", "WITHDRAWN"}
    if phase3 and stopped and item.meta.get("why_stopped"):
        out.append("PH3_STOPPED")
    elif stopped and item.meta.get("why_stopped"):
        # A stopped Phase 1/2 trial used to fall through to EARLY_RESULT and be
        # described as a "result"; it is a stop, and background-weight.
        out.append("TRIAL_STOPPED")
    if phase3 and readout:
        out.append("PH3_RESULT")
    elif phase2 and readout:
        out.append("PH2_RESULT")

    if "TRIAL_CLEARANCE" in study:
        # "Application for clinical trial ... approved by FDA" is permission to
        # begin, not a product approval. Both use the word approved, and one
        # real announcement of an IND clearance was graded P0 as a drug approval.
        out.append("REG_TRIAL_CLEARANCE")
    elif "APPROVAL" in study and item.meta.get("src_kind") in ("regulator", "company", "filing"):
        out.append("REG_APPROVAL")
    if "CRL" in study:
        out.append("REG_REJECTION")
    if "SUSPENDED" in study and "clinical hold" in (item.title + str(item.meta.get("body", ""))).lower():
        out.append("CLINICAL_HOLD")
    if "SAFETY_SIGNAL" in study:
        out.append("SAFETY_SERIOUS")
    elif "DILI_SIGNAL" in study:
        out.append("SAFETY_WATCH")
    if "SUBMISSION" in study:
        out.append("REG_SUBMISSION")
    if "DESIGNATION" in study:
        out.append("REG_DESIGNATION")
    if "ADCOM" in study:
        out.append("REG_MILESTONE")
    if "LICENSING" in study:
        out.append("DEAL")
    if "GUIDELINE" in study:
        out.append("GUIDELINE")
    if phase3 and "ENROLMENT" in study and not readout:
        out.append("PH3_START")
    if "PUBLICATION" in study and phase3:
        out.append("PIVOTAL_PUBLICATION")
    if not out:
        if study & {"PHASE1", "PRECLINICAL"}:
            out.append("EARLY_RESULT")
        elif study & {"ENROLMENT", "PHASE2", "PHASE3", "PHASE4"}:
            out.append("TRIAL_PROGRESS")
    seen: set[str] = set()
    return [s for s in out if not (s in seen or seen.add(s))]


def priority_for(signals: Iterable[str]) -> str:
    signals = set(signals)
    if signals & HARD_SIGNALS:
        return "P0"
    if signals & SOFT_SIGNALS:
        return "P1"
    if signals & BACKGROUND_SIGNALS:
        return "P2"
    return "P3"


def _recency_factor(item_date: str, today: str, halflife_days: float) -> float:
    try:
        d0 = date.fromisoformat(item_date)
        d1 = date.fromisoformat(today)
    except ValueError:
        return 1.0
    age = max((d1 - d0).days, 0)
    return 0.5 ** (age / max(halflife_days, 0.1))


def score_item(item: Item, priority: str, dm: DomainMap, today: str) -> float:
    weights = dm.weights
    line_weight = max((dm.line_weight(l) for l in item.lines), default=0.0)
    kind_weight = float(weights.get("src_kind", {}).get(item.meta.get("src_kind", ""), 0.5))
    tier = item.meta.get("company_tier")
    tier_weight = dm.company_tier_weight(int(tier)) if tier else 0.5
    recency = _recency_factor(item.date, today, float(weights.get("recency_halflife_days", 3.0)))

    score = BASE_SCORE.get(priority, 1.0) * max(line_weight, 0.1)
    score *= 0.6 + 0.4 * kind_weight
    score *= 0.6 + 0.4 * tier_weight
    score *= 0.7 + 0.3 * recency
    if item.meta.get("kols"):
        score += float(weights.get("kol_bonus", 0.2))
    if item.meta.get("conference"):
        score += float(weights.get("conference_window_bonus", 0.3))
    return round(score, 4)


def describe(signals: Iterable[str]) -> str:
    """Factual join of signal descriptions -- no judgement, no forecast."""
    parts = [str(SIGNALS[s]["desc"]) for s in signals if s in SIGNALS]
    return "; ".join(parts)


def grade(item: Item, dm: DomainMap | None = None, today: str | None = None,
          extra_signals: Iterable[str] | None = None) -> GradeResult:
    """Grade one item.  ``extra_signals`` carries the LLM step's findings."""
    dm = dm or load_cached()
    today = today or date.today().isoformat()

    signals = rule_signals(item)
    for signal in extra_signals or []:
        if signal in SIGNALS and signal not in signals:
            signals.append(signal)

    priority = priority_for(signals)
    main_hit = any(line in MAIN_LINES for line in item.lines)
    notes: list[str] = []

    if priority in ("P0", "P1") and is_periodic_report(item):
        notes.append(
            f"periodic financial report: capped from {priority} to P2, its events "
            "were announced separately")
        priority = "P2"

    if not item.lines:
        priority = "P3"
        notes.append("no line matched")
    elif not main_hit:
        # Auxiliary-only items never reach the daily report; grading caps them
        # so the selector does not have to special-case P0/P1.
        if priority in ("P0", "P1"):
            notes.append(
                f"auxiliary-only ({','.join(item.lines)}): capped from {priority} to P2, weekly only")
            priority = "P2"

    result = GradeResult(
        P=priority,
        signals=signals,
        why=describe(signals) or "no significance signal fired",
        main_line_hit=main_hit,
        notes=notes,
    )
    result.score = score_item(item, priority, dm, today)
    return result


def apply(item: Item, dm: DomainMap | None = None, today: str | None = None,
          extra_signals: Iterable[str] | None = None) -> Item:
    result = grade(item, dm=dm, today=today, extra_signals=extra_signals)
    item.P = result.P
    item.why = result.why
    item.score = result.score
    item.evidence.signals = sorted(set(item.evidence.signals) | set(result.signals))
    if result.notes:
        item.meta["grade_notes"] = result.notes
    item.meta["main_line_hit"] = result.main_line_hit
    return item
