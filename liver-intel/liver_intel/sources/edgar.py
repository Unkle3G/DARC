"""T2 -- SEC EDGAR 8-K.

Flow: ``company_tickers.json`` -> CIK -> ``submissions/CIK##########.json`` ->
8-K filings whose ``items`` include 7.01 (Reg FD), 8.01 (Other Events) or 2.02
(Results of Operations) -> the press-release exhibit (EX-99.x) body.

Access rules the SEC publishes are enforced upstream: the User-Agent carries a
contact address (``config.CONTACT``) and ``config.RATE_LIMITS`` keeps both
sec.gov hosts under the 10 requests/second ceiling.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable

from ..domain_map import Company
from ..models import Item, as_iso_date
from ..textutil import clip, html_to_text
from .base import BaseSource, Context

log = logging.getLogger(__name__)

WANTED_ITEMS = ("7.01", "8.01", "2.02")
TICKERS_ID = "sec.company_tickers"
SUBMISSIONS_ID = "sec.submissions"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
_EXHIBIT = re.compile(r"^ex[-_]?99", re.I)


class EdgarSource(BaseSource):
    id = "edgar"
    task = "T2"
    src_kind = "filing"

    def __init__(self, forms: Iterable[str] = ("8-K",), max_filings_per_company: int = 40):
        self.forms = tuple(forms)
        self.max_filings_per_company = max_filings_per_company

    # -- CIK resolution ---------------------------------------------------
    def _ticker_map(self, ctx: Context) -> dict[str, dict[str, Any]]:
        feed = ctx.registry.by_id(TICKERS_ID)
        if feed is None or not (feed.is_usable or ctx.settings.allow_unverified):
            ctx.note(f"[{self.id}] {TICKERS_ID} is not verified -- skipping EDGAR.")
            return {}
        response = ctx.fetcher.get(feed.url, allow_304=False)
        if not response.ok:
            ctx.note(f"[{self.id}] company_tickers.json returned HTTP {response.status}")
            return {}
        payload = json.loads(response.text)
        rows = payload.values() if isinstance(payload, dict) else payload
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            ticker = str(row.get("ticker", "")).upper()
            if ticker:
                out[ticker] = {"cik": int(row["cik_str"]), "title": row.get("title", "")}
        return out

    def _us_companies(self, ctx: Context) -> list[Company]:
        return [c for c in ctx.domain_map.companies
                if c.market in ("us", "global") and c.plain_ticker]

    # -- collection -------------------------------------------------------
    def collect(self, ctx: Context) -> list[Item]:
        base = ctx.registry.by_id(SUBMISSIONS_ID)
        if base is None or not (base.is_usable or ctx.settings.allow_unverified):
            ctx.note(f"[{self.id}] {SUBMISSIONS_ID} is not verified -- skipping EDGAR.")
            return []

        tickers = self._ticker_map(ctx)
        if not tickers:
            return []

        items: list[Item] = []
        for company in self._us_companies(ctx):
            entry = tickers.get((company.plain_ticker or "").upper())
            if entry is None:
                ctx.note(f"[{self.id}] no CIK for {company.name} ({company.ticker})")
                continue
            cik = entry["cik"]
            url = f"{base.url.rstrip('/')}/CIK{cik:010d}.json"
            try:
                response = ctx.fetcher.get(url, allow_304=False)
            except Exception as exc:
                ctx.note(f"[{self.id}] submissions fetch failed for {company.name}: {exc}")
                continue
            if not response.ok:
                ctx.note(f"[{self.id}] submissions HTTP {response.status} for {company.name}")
                continue
            items.extend(self._filings(ctx, company, cik, json.loads(response.text)))
        return self.emit(ctx, items)

    def _filings(self, ctx: Context, company: Company, cik: int,
                 submissions: dict[str, Any]) -> list[Item]:
        recent = (submissions.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        out: list[Item] = []
        for index, form in enumerate(forms[: self.max_filings_per_company]):
            if form not in self.forms:
                continue
            filing_items = str(_at(recent, "items", index) or "")
            wanted = [code for code in WANTED_ITEMS if code in filing_items]
            if not wanted:
                continue
            filed = as_iso_date(_at(recent, "filingDate", index))
            if not filed or (ctx.since and filed < ctx.since):
                continue
            accession = str(_at(recent, "accessionNumber", index) or "")
            if not accession:
                continue
            folder = accession.replace("-", "")
            primary = str(_at(recent, "primaryDocument", index) or "")
            filing_url = f"{ARCHIVES}/{cik}/{folder}/{primary}" if primary else \
                f"{ARCHIVES}/{cik}/{folder}/"

            item = Item(
                src=self.id,
                title=(_at(recent, "primaryDocDescription", index)
                       or f"{company.name} {form} ({', '.join(wanted)})"),
                url=filing_url,
                date=filed,
                meta={
                    "src_kind": self.src_kind,
                    "form": form,
                    "edgar_items": wanted,
                    "accession": accession,
                    "cik": cik,
                    "companies": [company.name],
                    "company_tier": company.tier,
                    "region": company.market,
                    "report_date": as_iso_date(_at(recent, "reportDate", index)) or filed,
                },
            )
            if ctx.store.is_seen(item.key):
                continue
            exhibit_url, body = self._press_release(ctx, cik, folder)
            if body:
                item.meta["body"] = clip(body, 40_000)
                item.meta["exhibit_url"] = exhibit_url
                headline = _first_headline(body)
                if headline:
                    item.title = f"{company.name}: {headline}"
            out.append(item)
        return out

    def _press_release(self, ctx: Context, cik: int, folder: str) -> tuple[str, str]:
        """Pull the EX-99.x press release out of the filing folder."""
        index_url = f"{ARCHIVES}/{cik}/{folder}/index.json"
        try:
            response = ctx.fetcher.get(index_url, allow_304=False)
        except Exception as exc:
            log.debug("exhibit index unreachable %s: %s", index_url, exc)
            return "", ""
        if not response.ok:
            return "", ""
        try:
            listing = json.loads(response.text)
        except json.JSONDecodeError:
            return "", ""

        names = [entry.get("name", "")
                 for entry in (listing.get("directory") or {}).get("item", [])]
        exhibits = [name for name in names
                    if _EXHIBIT.match(name) and name.lower().endswith((".htm", ".html", ".txt"))]
        if not exhibits:
            return "", ""
        exhibit_url = f"{ARCHIVES}/{cik}/{folder}/{sorted(exhibits)[0]}"
        try:
            exhibit = ctx.fetcher.get(exhibit_url, allow_304=False)
        except Exception as exc:
            log.debug("exhibit unreachable %s: %s", exhibit_url, exc)
            return "", ""
        if not exhibit.ok:
            return "", ""
        return exhibit_url, html_to_text(exhibit.text)


def _at(mapping: dict[str, Any], key: str, index: int) -> Any:
    values = mapping.get(key) or []
    return values[index] if index < len(values) else None


def _first_headline(body: str) -> str:
    for line in (body or "").splitlines():
        line = line.strip()
        if len(line) >= 25 and not line.lower().startswith(("exhibit", "ex-99", "for immediate")):
            return line[:200]
    return ""
