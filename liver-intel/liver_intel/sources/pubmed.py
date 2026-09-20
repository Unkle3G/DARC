"""Peer-reviewed literature via PubMed E-utilities.

Section 3 of the handover: ``sortpubdate`` can carry a *future* date for
ahead-of-print records, so items are archived by **collection date** and the
publication date is kept in meta for reference only.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable
from urllib.parse import urlencode

from ..models import Item, as_iso_date
from ..people import abstracts_from_pubmed_xml, authors_from_pubmed_xml
from ..textutil import clip
from .base import BaseSource, Context

log = logging.getLogger(__name__)

FEED_ID = "pubmed.esummary"
ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

QUERY_TERMS = (
    '"hepatitis B"[Title/Abstract]', '"hepatitis D"[Title/Abstract]',
    '"MASH"[Title/Abstract]', '"NASH"[Title/Abstract]', '"MASLD"[Title/Abstract]',
    '"primary biliary cholangitis"[Title/Abstract]',
    '"hepatocellular carcinoma"[Title/Abstract]', '"cirrhosis"[Title/Abstract]',
)

#: Only journals whose content is peer-reviewed and published count; preprint
#: servers are excluded upstream by models.EXCLUDED_HOST_SUBSTRINGS.
DEFAULT_DAYS = 3

#: How many records the window may yield in total, and how many ids go into one
#: esummary/efetch call. The query returned 241 hits over a three-day window
#: against a retmax of 50, so four of every five papers were dropped -- and
#: because esearch sorts by index date, *which* fifth survived depended on the
#: minute the run fired. A paper present in one run was gone from the next an
#: hour later. The window is paged through instead; the cap only guards against
#: a query that has gone wrong.
MAX_IDS = 600
BATCH = 150


#: PubMed publication types that announce a change to the record rather than a
#: finding. They carry a title and an abstract like any article, so nothing
#: downstream can tell them apart -- the catalogue's own label is the only
#: honest signal.
#: What the catalogue calls a correction notice varies between the indexed
#: publication type and what esummary hands back, so both spellings are here:
#: "RETRACTION: Long Noncoding RNA NR2F1-AS1..." arrived carrying the
#: non-standard "Retraction Notice" and sailed past the standard names.
NOT_NEWS = frozenset({"Published Erratum", "Retraction of Publication",
                      "Retracted Publication", "Retraction Notice",
                      "Corrected and Republished Article",
                      "Expression of Concern"})


class PubmedSource(BaseSource):
    id = "pubmed"
    task = "T4"
    src_kind = "journal"

    def __init__(self, terms: Iterable[str] = QUERY_TERMS, retmax: int = MAX_IDS):
        self.terms = tuple(terms)
        self.retmax = retmax

    def collect(self, ctx: Context) -> list[Item]:
        feed = ctx.registry.by_id(FEED_ID)
        if feed is None or not (feed.is_usable or ctx.settings.allow_unverified):
            ctx.note(f"[{self.id}] {FEED_ID} is not verified -- literature skipped.")
            return []

        ids, total = self._search(ctx)
        if not ids:
            return []
        if total > len(ids):
            ctx.note(f"[{self.id}] window holds {total} records; took {len(ids)} "
                     f"(cap {self.retmax}) -- raise MAX_IDS or narrow the query")

        out: list[Item] = []
        for start in range(0, len(ids), BATCH):
            batch = ids[start:start + BATCH]
            payload = self._summaries(ctx, feed.url, batch)
            if not payload:
                continue
            # esummary carries names but no affiliations and no abstract; one
            # efetch call per batch supplies both.
            authorship, abstracts = self._efetch(ctx, batch)
            out.extend(self._items(ctx, payload, authorship, abstracts))
        return out

    def _search(self, ctx: Context) -> tuple[list[str], int]:
        """Every id in the window, paged, newest first."""
        query = " OR ".join(self.terms)
        ids: list[str] = []
        total = 0
        while len(ids) < self.retmax:
            params = urlencode({
                "db": "pubmed", "term": query, "retmode": "json",
                "retstart": str(len(ids)),
                "retmax": str(min(BATCH, self.retmax - len(ids))),
                "sort": "date", "datetype": "edat", "reldate": str(DEFAULT_DAYS)})
            search_url = f"{ESEARCH}?{params}"
            try:
                response = ctx.fetcher.get(search_url, allow_304=False)
            except Exception as exc:
                ctx.note(f"[{self.id}] esearch failed: {exc}")
                break
            if not response.ok:
                ctx.note(f"[{self.id}] esearch HTTP {response.status}")
                break
            try:
                result = response.json().get("esearchresult", {})
            except json.JSONDecodeError:
                break
            total = int(result.get("count") or 0)
            page = result.get("idlist") or []
            if not page:
                break
            ids.extend(page)
            if len(ids) >= total:
                break
        return ids, total

    def _summaries(self, ctx: Context, url: str, ids: list[str]) -> dict[str, Any]:
        summary_url = f"{url}?{urlencode({'db': 'pubmed', 'id': ','.join(ids), 'retmode': 'json'})}"
        try:
            summary = ctx.fetcher.get(summary_url, allow_304=False)
        except Exception as exc:
            ctx.note(f"[{self.id}] esummary failed: {exc}")
            return {}
        if not summary.ok:
            return {}
        try:
            return summary.json().get("result", {})
        except json.JSONDecodeError:
            return {}

    def _items(self, ctx: Context, payload: dict[str, Any],
               authorship: dict[str, list[dict[str, Any]]],
               abstracts: dict[str, str]) -> list[Item]:
        out: list[Item] = []
        for pmid in payload.get("uids", []) or []:
            record = payload.get(pmid) or {}
            item = self._to_item(ctx, pmid, record, abstracts.get(str(pmid), ""))
            if item is None:
                continue
            people = authorship.get(str(pmid))
            if people:
                item.meta["contributors"] = people
                item.meta["first_author"] = people[0]["name"]
                if people[0].get("affiliation"):
                    item.meta["first_author_affiliation"] = people[0]["affiliation"]
            out.append(item)
        return self.emit(ctx, out)

    def _efetch(self, ctx: Context, ids: list[str]
                ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
        """Authors-with-affiliations and abstracts, from one call."""
        url = f"{EFETCH}?{urlencode({'db': 'pubmed', 'id': ','.join(ids), 'retmode': 'xml'})}"
        try:
            response = ctx.fetcher.get(url, allow_304=False)
        except Exception as exc:
            ctx.note(f"[{self.id}] efetch failed, no authors and no abstracts: {exc}")
            return {}, {}
        if not response.ok:
            ctx.note(f"[{self.id}] efetch HTTP {response.status}, "
                     f"no authors and no abstracts")
            return {}, {}
        return (authors_from_pubmed_xml(response.text),
                abstracts_from_pubmed_xml(response.text))

    def _to_item(self, ctx: Context, pmid: str, record: dict[str, Any],
                 abstract: str = "") -> Item | None:
        title = (record.get("title") or "").strip()
        if not title:
            return None
        pub_date = as_iso_date(record.get("sortpubdate") or record.get("pubdate"))
        item = Item(
            src=self.id,
            title=title,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            # Archived by collection date: sortpubdate may sit in the future for
            # ahead-of-print records (handover section 3).
            date=ctx.today,
            meta={
                "src_kind": self.src_kind,
                "pmid": pmid,
                "journal": record.get("fulljournalname") or record.get("source"),
                "pub_date": pub_date,
                "pub_date_is_future": bool(pub_date and pub_date > ctx.today),
                "authors": [a.get("name") for a in (record.get("authors") or [])][:12],
                # A journal entry used to carry no record line at all next to a
                # registry entry's six fields. These are the catalogue's own
                # facts about what the paper *is*: PubMed's publication types
                # (Review, Case Reports, Editorial) and the DOI.
                "publication_types": [t for t in (record.get("pubtype") or [])
                                      if t and t != "Journal Article"],
                "doi": _doi(record.get("elocationid")),
                # The abstract is what the journal published about the study, so
                # it is both what the tagger reads and what may be quoted. Without
                # it the tagger saw a bare title, fired PUBLICATION alone, and
                # every article in the literature graded P3.
                "abstract": clip(abstract, 8000),
                "has_abstract": bool(abstract),
                "body": clip("\n".join(filter(None, [title, abstract])), 10_000),
                "quotable": clip("\n".join(filter(None, [title, abstract])), 10_000),
            },
        )
        if set(item.meta["publication_types"]) & NOT_NEWS:
            # A correction notice is not a paper. "Corrigendum to: COMMD10
            # inhibits HIF1a/CP loop..." collected and published as an item of
            # its own; the catalogue already says what it is.
            return None
        item.study.append("PUBLICATION")
        if ctx.store.is_seen(item.key):
            return None
        return item


def _doi(elocationid: str | None) -> str:
    """esummary writes the DOI as ``doi: 10.1097/HEP...`` in ``elocationid``."""
    text = (elocationid or "").strip()
    if not text.lower().startswith("doi:"):
        return ""
    return text.split(":", 1)[1].strip()
