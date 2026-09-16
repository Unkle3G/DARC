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
from ..textutil import clip, html_to_text, lead
from .base import BaseSource, Context

log = logging.getLogger(__name__)

WANTED_ITEMS = ("7.01", "8.01", "2.02")

#: A foreign private issuer files 6-K where a domestic one files 8-K -- Novo
#: Nordisk, GSK, AstraZeneca and Takeda filed no 8-K at all over a recent
#: quarter, only 6-K. A 6-K carries no Item codes, so the item filter that
#: narrows 8-K to Reg FD / Other Events / Results cannot apply to it; the line
#: tagger does that work instead.
ITEMLESS_FORMS = ("6-K",)
TICKERS_ID = "sec.company_tickers"
SUBMISSIONS_ID = "sec.submissions"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
#: Rows of the filing index table: Seq | Description | Document | Type | Size.
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
#: A document name inside the Document cell, which can carry trailing notes
#: such as "mdgl-20260811.htm   iXBRL".
_DOC_NAME = re.compile(r"[\w.\-]+\.(?:htm|html|txt)", re.I)
#: Filers name the press-release exhibit whatever they like -- one real filing
#: called it `pressrelease-boardappointm.htm` -- so it is found by its declared
#: exhibit TYPE, never by guessing the filename.
_EX99_TYPE = re.compile(r"^ex-?99", re.I)


class EdgarSource(BaseSource):
    id = "edgar"
    task = "T2"
    src_kind = "filing"

    def __init__(self, forms: Iterable[str] = ("8-K", "6-K"),
                 max_filings_per_company: int = 40):
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

    #: Markets whose companies file with an exchange rather than the SEC.
    NON_SEC_MARKETS = ("hk", "cn")

    def _sec_companies(self, ctx: Context) -> list[Company]:
        """Roster companies that file with the SEC.

        Not a geographic test: a European issuer with a US listing files there
        too, as a foreign private issuer. Filtering on market "us"/"global" left
        Novo Nordisk, Roche, Inventiva and GENFIT out of EDGAR entirely even
        though several of them file every week.
        """
        return [c for c in ctx.domain_map.companies
                if c.market not in self.NON_SEC_MARKETS and c.sec_filer
                and (c.plain_ticker or c.cik)]

    # -- collection -------------------------------------------------------
    def collect(self, ctx: Context) -> list[Item]:
        base = ctx.registry.by_id(SUBMISSIONS_ID)
        if base is None or not (base.is_usable or ctx.settings.allow_unverified):
            ctx.note(f"[{self.id}] {SUBMISSIONS_ID} is not verified -- skipping EDGAR.")
            return []

        companies = self._sec_companies(ctx)
        tickers = self._ticker_map(ctx)
        # No early exit when the lookup comes back empty: a company with a pinned
        # CIK is still reachable, and one without ends up in ``unresolved`` below,
        # which is how an operator learns the roster needs a CIK.

        items: list[Item] = []
        unresolved: list[str] = []
        for company in companies:
            entry = tickers.get((company.plain_ticker or "").upper())
            cik = company.cik or (entry["cik"] if entry else None)
            if cik is None:
                unresolved.append(f"{company.name} ({company.ticker})")
                continue
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
        if unresolved:
            ctx.note(
                f"[{self.id}] no CIK for: {', '.join(unresolved)}. company_tickers.json "
                f"lists only currently-listed tickers, so an acquired or delisted "
                f"filer needs its CIK pinned in the roster.")
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
            if not wanted and form not in ITEMLESS_FORMS:
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
                    "form_is_itemless": form in ITEMLESS_FORMS,
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
            exhibit_url, body = self._press_release(ctx, cik, folder, accession)
            if body:
                item.meta["body"] = clip(body, 40_000)
                item.meta["exhibit_url"] = exhibit_url
                headline = _first_headline(body)
                if headline:
                    item.title = f"{company.name}: {headline}"
            out.append(item)
        return out

    def _press_release(self, ctx: Context, cik: int, folder: str,
                       accession: str) -> tuple[str, str]:
        """Pull the EX-99.x press release out of the filing folder.

        The filing index page carries the authoritative Type column, which is
        the only reliable way to tell which document is the press release.
        """
        index_url = f"{ARCHIVES}/{cik}/{folder}/{accession}-index.htm"
        try:
            response = ctx.fetcher.get(index_url, allow_304=False)
        except Exception as exc:
            log.debug("filing index unreachable %s: %s", index_url, exc)
            return "", ""
        if not response.ok:
            return "", ""

        name = exhibit_name(response.text)
        if not name:
            return "", ""
        exhibit_url = f"{ARCHIVES}/{cik}/{folder}/{name}"
        try:
            exhibit = ctx.fetcher.get(exhibit_url, allow_304=False)
        except Exception as exc:
            log.debug("exhibit unreachable %s: %s", exhibit_url, exc)
            return "", ""
        if not exhibit.ok:
            return "", ""
        return exhibit_url, html_to_text(exhibit.text)


def exhibit_name(index_html: str) -> str:
    """Filename of the press-release exhibit, by declared type.

    Prefers EX-99.1 and falls back to any other EX-99.x, which is how a filer
    that splits the release across exhibits still resolves.
    """
    candidates: list[tuple[str, str]] = []
    for row in _ROW.findall(index_html or ""):
        cells = [_strip(cell) for cell in _CELL.findall(row)]
        if len(cells) < 4:
            continue
        document, kind = cells[2], cells[3]
        if not _EX99_TYPE.match(kind):
            continue
        match = _DOC_NAME.search(document)
        if match:
            candidates.append((kind.upper(), match.group(0)))
    if not candidates:
        return ""
    for kind, name in candidates:
        if kind in ("EX-99.1", "EX-991", "EX99.1"):
            return name
    return candidates[0][1]


def _strip(cell: str) -> str:
    import html as html_lib

    return html_lib.unescape(re.sub(r"<[^>]+>", " ", cell)).strip()


def _at(mapping: dict[str, Any], key: str, index: int) -> Any:
    values = mapping.get(key) or []
    return values[index] if index < len(values) else None


def _first_headline(body: str) -> str:
    """The release's own headline.

    EDGAR prepends the exhibit wrapper -- type, sequence number, filename -- and
    a filename like `a20260805-q2ex991earningsr.htm` is long enough to look like
    a headline, which is how one filing ended up titled after its own file.
    ``lead`` already drops that wrapper.
    """
    for line in lead(body, limit=400).splitlines():
        line = line.strip()
        if len(line) >= 25 and not line.lower().startswith(("exhibit", "ex-99",
                                                            "for immediate")):
            return line[:200]
    return ""
