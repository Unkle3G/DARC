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
from ..textutil import clip
from .base import BaseSource, Context

log = logging.getLogger(__name__)

FEED_ID = "pubmed.esummary"
ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"

QUERY_TERMS = (
    '"hepatitis B"[Title/Abstract]', '"hepatitis D"[Title/Abstract]',
    '"MASH"[Title/Abstract]', '"NASH"[Title/Abstract]', '"MASLD"[Title/Abstract]',
    '"primary biliary cholangitis"[Title/Abstract]',
    '"hepatocellular carcinoma"[Title/Abstract]', '"cirrhosis"[Title/Abstract]',
)

#: Only journals whose content is peer-reviewed and published count; preprint
#: servers are excluded upstream by models.EXCLUDED_HOST_SUBSTRINGS.
DEFAULT_DAYS = 3


class PubmedSource(BaseSource):
    id = "pubmed"
    task = "T4"
    src_kind = "journal"

    def __init__(self, terms: Iterable[str] = QUERY_TERMS, retmax: int = 50):
        self.terms = tuple(terms)
        self.retmax = retmax

    def collect(self, ctx: Context) -> list[Item]:
        feed = ctx.registry.by_id(FEED_ID)
        if feed is None or not (feed.is_usable or ctx.settings.allow_unverified):
            ctx.note(f"[{self.id}] {FEED_ID} is not verified -- literature skipped.")
            return []

        query = " OR ".join(self.terms)
        search_url = f"{ESEARCH}?{urlencode({'db': 'pubmed', 'term': query, 'retmode': 'json', 'retmax': str(self.retmax), 'sort': 'date', 'datetype': 'edat', 'reldate': str(DEFAULT_DAYS)})}"
        try:
            response = ctx.fetcher.get(search_url, allow_304=False)
        except Exception as exc:
            ctx.note(f"[{self.id}] esearch failed: {exc}")
            return []
        if not response.ok:
            ctx.note(f"[{self.id}] esearch HTTP {response.status}")
            return []
        try:
            ids = response.json().get("esearchresult", {}).get("idlist", []) or []
        except json.JSONDecodeError:
            return []
        if not ids:
            return []

        summary_url = f"{feed.url}?{urlencode({'db': 'pubmed', 'id': ','.join(ids), 'retmode': 'json'})}"
        try:
            summary = ctx.fetcher.get(summary_url, allow_304=False)
        except Exception as exc:
            ctx.note(f"[{self.id}] esummary failed: {exc}")
            return []
        if not summary.ok:
            return []
        try:
            payload = summary.json().get("result", {})
        except json.JSONDecodeError:
            return []

        out: list[Item] = []
        for pmid in payload.get("uids", []) or []:
            record = payload.get(pmid) or {}
            item = self._to_item(ctx, pmid, record)
            if item is not None:
                out.append(item)
        return self.emit(ctx, out)

    def _to_item(self, ctx: Context, pmid: str, record: dict[str, Any]) -> Item | None:
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
                "body": clip(title, 2000),
            },
        )
        item.study.append("PUBLICATION")
        if ctx.store.is_seen(item.key):
            return None
        return item
