"""T3 -- ClinicalTrials.gov, driven by *state changes*.

The previous version pulled on ``LastUpdatePostDate``, which surfaces every
registry touch -- a contact-email edit produced an item.  This adapter keeps the
last seen status per NCT in SQLite and emits only when one of

    overallStatus | resultsFirstPostDate | whyStopped

actually changes.  A trial seen for the first time is recorded silently unless
it is already in a state worth reporting.

Phase 3 TERMINATED / SUSPENDED / WITHDRAWN with a non-empty ``whyStopped`` is a
P0 by the grading rules; this adapter's job is to hand the grader the fields.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from ..models import Item, Quote, as_iso_date
from ..store import NctState
from .base import BaseSource, Context

log = logging.getLogger(__name__)

FEED_ID = "ctgov.v2"
STOPPED = {"TERMINATED", "SUSPENDED", "WITHDRAWN"}
PAGE_SIZE = 100

#: Registry query terms.  Conditions only -- line tagging happens later.
CONDITION_TERMS = (
    "hepatitis B", "hepatitis D", "MASH", "NASH", "MASLD",
    "primary biliary cholangitis", "primary sclerosing cholangitis",
    "hepatocellular carcinoma", "liver cirrhosis", "portal hypertension",
    "alcoholic hepatitis", "acute-on-chronic liver failure", "liver transplantation",
)

FIELDS = (
    "NCTId", "BriefTitle", "OverallStatus", "WhyStopped", "Phase",
    "LastUpdatePostDate", "ResultsFirstPostDate", "LeadSponsorName",
    "Condition", "StudyType", "PrimaryCompletionDate",
)


class CtGovSource(BaseSource):
    id = "ctgov"
    task = "T3"
    src_kind = "registry"

    def __init__(self, terms: Iterable[str] = CONDITION_TERMS, max_pages: int = 6):
        self.terms = tuple(terms)
        self.max_pages = max_pages

    def collect(self, ctx: Context) -> list[Item]:
        feed = ctx.registry.by_id(FEED_ID)
        if feed is None or not (feed.is_usable or ctx.settings.allow_unverified):
            ctx.note(f"[{self.id}] {FEED_ID} is not verified -- skipping the registry.")
            return []

        items: list[Item] = []
        seen_nct: set[str] = set()
        for term in self.terms:
            for study in self._studies(ctx, feed.url, term):
                nct = study.get("nct_id")
                if not nct or nct in seen_nct:
                    continue
                seen_nct.add(nct)
                item = self._diff_to_item(ctx, study)
                if item is not None:
                    items.append(item)
        ctx.note(f"[{self.id}] examined {len(seen_nct)} studies, "
                 f"{len(items)} had a reportable state change")
        return self.emit(ctx, items)

    # -- API --------------------------------------------------------------
    def _studies(self, ctx: Context, base_url: str, term: str) -> list[dict[str, Any]]:
        from urllib.parse import urlencode

        out: list[dict[str, Any]] = []
        token: str | None = None
        for _ in range(self.max_pages):
            params = {
                "query.cond": term,
                "pageSize": str(PAGE_SIZE),
                "fields": "|".join(FIELDS),
                "format": "json",
            }
            if ctx.since:
                # Narrow the sweep; the state table still decides what is emitted.
                params["filter.advanced"] = f"AREA[LastUpdatePostDate]RANGE[{ctx.since},MAX]"
            if token:
                params["pageToken"] = token
            url = f"{base_url}?{urlencode(params)}"
            try:
                response = ctx.fetcher.get(url, allow_304=False)
            except Exception as exc:
                ctx.note(f"[{self.id}] query failed for {term!r}: {exc}")
                return out
            if not response.ok:
                ctx.note(f"[{self.id}] HTTP {response.status} for {term!r}")
                return out
            payload = response.json()
            for study in payload.get("studies", []) or []:
                out.append(_flatten(study))
            token = payload.get("nextPageToken")
            if not token:
                break
        return out

    # -- state diffing ----------------------------------------------------
    def _diff_to_item(self, ctx: Context, study: dict[str, Any]) -> Item | None:
        nct = study["nct_id"]
        current = NctState(
            nct_id=nct,
            overall_status=study.get("overall_status"),
            why_stopped=study.get("why_stopped"),
            results_first_posted=study.get("results_first_posted"),
            last_update_posted=study.get("last_update_posted"),
            phase=study.get("phase"),
        )
        previous = ctx.store.get_nct(nct)
        ctx.store.put_nct(current)

        if previous is None:
            # First sighting: only report if it is already newsworthy, otherwise
            # the first run after deployment would emit the whole registry.
            if not (current.overall_status in STOPPED and current.why_stopped):
                return None
            changes = {"overall_status": (None, current.overall_status)}
        else:
            changes = previous.diff(current)
            if not changes:
                return None

        url = f"https://clinicaltrials.gov/study/{nct}"
        change_text = "; ".join(
            f"{field}: {before or 'none'} -> {after or 'none'}"
            for field, (before, after) in changes.items()
        )
        item = Item(
            src=self.id,
            title=study.get("title") or nct,
            url=url,
            date=ctx.today,       # archived by collection date
            meta={
                "src_kind": self.src_kind,
                "nct_id": nct,
                "overall_status": current.overall_status,
                "why_stopped": current.why_stopped,
                "phase": current.phase,
                "sponsor": study.get("sponsor"),
                "conditions": study.get("conditions"),
                "last_update_posted": current.last_update_posted,
                "results_first_posted": current.results_first_posted,
                "state_changes": change_text,
                # ``body`` feeds the tagger and carries engine-written scaffolding
                # ("Phase: PHASE3"). ``quotable`` is only what the registry itself
                # published, so evidence quotes cannot end up citing our own
                # summary lines back as if they were source text.
                "body": "\n".join(filter(None, [
                    study.get("title"),
                    f"Overall status: {current.overall_status}",
                    f"Why stopped: {current.why_stopped}" if current.why_stopped else "",
                    f"Phase: {current.phase}" if current.phase else "",
                    f"Conditions: {', '.join(study.get('conditions') or [])}",
                ])),
                "quotable": "\n".join(filter(None, [
                    study.get("title"), current.why_stopped,
                    current.overall_status, current.phase,
                ])),
            },
        )
        # A registry record states its facts in fields, not prose, so its evidence
        # is the published field values with the field named as the locator.
        # Quoting them this way keeps "signal + 原文依据" honest: each one is
        # verbatim and checkable by opening the NCT page, and nothing is
        # paraphrased into a sentence the registry never wrote.
        for label, value in (("overallStatus", current.overall_status),
                             ("phases", current.phase),
                             ("whyStopped", current.why_stopped)):
            if value:
                item.evidence.quotes.append(
                    Quote(text=str(value), url=url,
                          locator=f"ClinicalTrials.gov · {label}"))

        # Give the grader the phase and stop tags directly; the text tagger sees
        # the same facts but the registry states them structurally.
        if _is_phase3(current.phase):
            item.study.append("PHASE3")
        if (current.overall_status or "") in STOPPED:
            item.study.append(current.overall_status)
        # Results already on file when we first see a study are not a readout:
        # only a posting that actually happened in this window is news.
        results_are_new = (
            previous is not None
            and previous.results_first_posted != current.results_first_posted
        ) or (
            previous is None and ctx.since is not None
            and (current.results_first_posted or "") >= ctx.since
        )
        if current.results_first_posted and results_are_new:
            item.study.append("TOPLINE")
        return item


def _is_phase3(phase: str | None) -> bool:
    return "3" in (phase or "") or "III" in (phase or "")


def _flatten(study: dict[str, Any]) -> dict[str, Any]:
    """v2 responses nest under protocolSection; tolerate the flat shape too."""
    protocol = study.get("protocolSection") or study
    ident = protocol.get("identificationModule") or {}
    status = protocol.get("statusModule") or {}
    design = protocol.get("designModule") or {}
    sponsor = (protocol.get("sponsorCollaboratorsModule") or {}).get("leadSponsor") or {}
    conditions = (protocol.get("conditionsModule") or {}).get("conditions") or []
    phases = design.get("phases") or []
    return {
        "nct_id": ident.get("nctId") or study.get("NCTId"),
        "title": ident.get("briefTitle") or study.get("BriefTitle"),
        "overall_status": status.get("overallStatus") or study.get("OverallStatus"),
        "why_stopped": status.get("whyStopped") or study.get("WhyStopped"),
        "last_update_posted": as_iso_date(
            (status.get("lastUpdatePostDateStruct") or {}).get("date")
            or study.get("LastUpdatePostDate")),
        "results_first_posted": as_iso_date(
            (status.get("resultsFirstPostDateStruct") or {}).get("date")
            or study.get("ResultsFirstPostDate")),
        "phase": ", ".join(phases) if phases else study.get("Phase"),
        "sponsor": sponsor.get("name") or study.get("LeadSponsorName"),
        "conditions": conditions or study.get("Condition") or [],
    }
