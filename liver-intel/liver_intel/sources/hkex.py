"""T5 -- HKEX (披露易) statutory announcements.

The endpoints come from the site's own configuration
(``/ncms/eds/titlesearch/config.js``), not from guesswork:

* ``prefix.do``       stock code -> the internal ``stockId`` the search needs
* ``titlesearch.xhtml`` the announcement search itself, which answers plain HTTP
  with an HTML result table

That config also states the limits the search enforces, so the adapter honours
them rather than discovering them by getting rejected: a single-stock query
spans at most 12 months.
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import re
from datetime import date, timedelta

from urllib.parse import urlencode

from ..domain_map import Company
from ..models import Item
from ..textutil import clip, pdf_text
from .base import BaseSource, Context


log = logging.getLogger(__name__)

PREFIX_ID = "hkex.prefix"
STOCKLIST_ID = "hkex.activestock"
SEARCH_ID = "hkex.titlesearch"
DOC_BASE = "https://www1.hkexnews.hk"

#: config.js: SearchDocSingleMaxMonthRange = 12
MAX_MONTHS_SINGLE_STOCK = 12

#: The search headline is a category label, not a description -- an issuer's
#: clinical news arrives as "Announcements and Notices - [Other - Business
#: Update]". Filtering those labels for disease words therefore matches nothing;
#: what is filtered instead is the routine administrative categories, and every
#: remaining announcement is opened, because the news is only inside the PDF.
ROUTINE_HEADLINES = (
    "monthly return", "next day disclosure", "share buyback", "proxy form",
    "date of board meeting", "notification letter", "reply form", "poll result",
    "list of directors", "constitutional documents", "annual report",
    "interim report", "circular", "notice of annual general meeting",
    "notice of extraordinary general meeting", "change of", "grant of share options",
)

#: A cap on how many announcements are opened per run: each is a PDF fetch.
MAX_PDFS_PER_RUN = 40

_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(
    r'<td[^>]*class="[^"]*\b(release-time|stock-short-code|stock-short-name)\b[^"]*"[^>]*>(.*?)</td>',
    re.S | re.I)
_HEADLINE = re.compile(r'<div[^>]*class="[^"]*\bheadline\b[^"]*"[^>]*>(.*?)</div>', re.S | re.I)
_DOC_LINK = re.compile(r'<a[^>]+href="(/listedco/[^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
#: Each cell repeats its own column label for the mobile layout; it is markup
#: furniture, not part of the value.
_MOBILE_LABEL = re.compile(
    r'<span[^>]*class="[^"]*\bmobile-list-heading\b[^"]*"[^>]*>.*?</span>', re.S | re.I)


def _text(fragment: str) -> str:
    fragment = _MOBILE_LABEL.sub(" ", fragment or "")
    return html_lib.unescape(_TAG.sub(" ", fragment)).replace("\xa0", " ").strip()


def _iso(release_time: str) -> str | None:
    """'16/09/2026 18:04' -> '2026-09-16'."""
    match = re.search(r"(\d{2})/(\d{2})/(\d{4})", release_time or "")
    if not match:
        return None
    day, month, year = match.groups()
    return f"{year}-{month}-{day}"


def parse_results(html: str) -> list[dict[str, str]]:
    """Rows of the announcement table, as published."""
    out: list[dict[str, str]] = []
    for row in _ROW.findall(html or ""):
        fields = {name: _text(value) for name, value in _CELL.findall(row)}
        if "release-time" not in fields:
            continue
        headline = _HEADLINE.search(row)
        link = _DOC_LINK.search(row)
        if not link:
            continue
        out.append({
            "date": _iso(fields.get("release-time", "")) or "",
            "code": fields.get("stock-short-code", ""),
            "short_name": fields.get("stock-short-name", ""),
            "headline": _text(headline.group(1)) if headline else "",
            "doc_title": _text(link.group(2)),
            "url": DOC_BASE + link.group(1),
        })
    return out


class HkexSource(BaseSource):
    id = "hkex"
    task = "T5"
    src_kind = "filing"
    venue = "HKEX"

    def __init__(self, months: int = 3):
        self.months = min(months, MAX_MONTHS_SINGLE_STOCK)
        self._codes: dict[str, int] | None = None

    # -- stock code -> stockId -------------------------------------------
    def _stock_table(self, ctx: Context) -> dict[str, int]:
        """The exchange's own list of active securities, code -> stockId.

        ``prefix.do`` is a *prefix* match: querying 1558 returns the derivative
        warrants 15580, 15581 ... and pushes the security itself off the first
        page, so an exact code can simply not come back. The full list has no
        such ambiguity.
        """
        if self._codes is not None:
            return self._codes
        self._codes = {}
        feed = ctx.registry.by_id(STOCKLIST_ID)
        if feed is None or not (feed.is_usable or ctx.settings.allow_unverified):
            return self._codes
        try:
            response = ctx.fetcher.get(feed.url, allow_304=False)
            rows = response.json()
        except Exception as exc:
            ctx.note(f"[{self.id}] active-stock list unreadable: {exc}")
            return self._codes
        for row in rows if isinstance(rows, list) else []:
            code, stock_id = row.get("c"), row.get("i")
            if code and stock_id:
                self._codes[str(code).zfill(5)] = int(stock_id)
        return self._codes

    def _stock_id(self, ctx: Context, prefix_url: str, code: str) -> int | None:
        table = self._stock_table(ctx)
        found = table.get(code.zfill(5))
        if found is not None:
            return found
        if table:
            ctx.note(f"[{self.id}] {code} is not in the exchange's active-securities "
                     f"list -- delisted, renamed, or never SEHK-listed")
            return None
        return self._stock_id_by_prefix(ctx, prefix_url, code)

    def _stock_id_by_prefix(self, ctx: Context, prefix_url: str, code: str) -> int | None:
        query = urlencode({"callback": "callback", "lang": "EN", "type": "A",
                           "name": code, "market": "SEHK"})
        try:
            response = ctx.fetcher.get(f"{prefix_url}{query}", allow_304=False)
        except Exception as exc:
            ctx.note(f"[{self.id}] stock lookup failed for {code}: {exc}")
            return None
        if not response.ok:
            return None
        body = response.text.strip()
        match = re.search(r"callback\((.*)\)\s*;?\s*$", body, re.S)
        try:
            payload = json.loads(match.group(1) if match else body)
        except (json.JSONDecodeError, AttributeError):
            return None
        wanted = code.zfill(5)
        for entry in payload.get("stockInfo", []) or []:
            if str(entry.get("code", "")).zfill(5) == wanted:
                return int(entry["stockId"])
        return None

    # -- collection -------------------------------------------------------
    def collect(self, ctx: Context) -> list[Item]:
        prefix = ctx.registry.by_id(PREFIX_ID)
        search = ctx.registry.by_id(SEARCH_ID)
        for feed in (prefix, search):
            if feed is None or not (feed.is_usable or ctx.settings.allow_unverified):
                ctx.note(f"[{self.id}] HKEX endpoints are not verified -- skipped.")
                return []

        start = (date.fromisoformat(ctx.since) if ctx.since
                 else date.fromisoformat(ctx.today) - timedelta(days=30 * self.months))
        floor = date.fromisoformat(ctx.today) - timedelta(days=30 * MAX_MONTHS_SINGLE_STOCK)
        if start < floor:
            ctx.note(f"[{self.id}] window trimmed to {MAX_MONTHS_SINGLE_STOCK} months, "
                     f"the limit the search itself declares")
            start = floor

        ctx.pdf_budget = MAX_PDFS_PER_RUN
        out: list[Item] = []
        for company in ctx.domain_map.companies_for_market("hk"):
            code = (company.ticker or "").split(".")[0]
            if not code:
                continue
            stock_id = self._stock_id(ctx, prefix.url, code)
            if stock_id is None:
                ctx.note(f"[{self.id}] no stockId for {company.name} ({company.ticker})")
                continue
            out.extend(self._announcements(ctx, search.url, company, stock_id, start))
        return self.emit(ctx, out)

    @staticmethod
    def _pdf(ctx: Context, url: str) -> str:
        session = getattr(ctx.fetcher, "session", None)
        if session is None:
            return ""
        try:
            response = session.get(url, timeout=60)
            response.raise_for_status()
        except Exception as exc:
            log.info("announcement PDF unreadable %s: %s", url, exc)
            return ""
        if len(response.content) > 12_000_000:
            return ""
        return pdf_text(response.content)

    def _announcements(self, ctx: Context, search_url: str, company: Company,
                       stock_id: int, start: date) -> list[Item]:
        query = urlencode({
            "lang": "EN", "category": "0", "market": "SEHK", "searchType": "1",
            "documentType": "-1", "t1code": "-2", "t2Gcode": "-2", "t2code": "-2",
            "stockId": str(stock_id),
            "from": start.strftime("%Y%m%d"),
            "to": date.fromisoformat(ctx.today).strftime("%Y%m%d"),
            "MB-Daterange": "0", "title": "",
        })
        try:
            response = ctx.fetcher.get(f"{search_url}?{query}", allow_304=False)
        except Exception as exc:
            ctx.note(f"[{self.id}] search failed for {company.name}: {exc}")
            return []
        if not response.ok:
            ctx.note(f"[{self.id}] search HTTP {response.status} for {company.name}")
            return []

        out: list[Item] = []
        for row in parse_results(response.text):
            headline = row["headline"] or row["doc_title"]
            if not headline or not row["date"]:
                continue
            if any(term in headline.lower() for term in ROUTINE_HEADLINES):
                continue
            body = ""
            if ctx.pdf_budget > 0:
                body = self._pdf(ctx, row["url"])
                ctx.pdf_budget -= 1
            # ``doc_title`` is the document's own linked title ("2026 Interim
            # Report"); ``headline`` is the exchange's filing category
            # ("Financial Statements/ESG Information - [Interim/Half-Year
            # Report]"), which is kept in meta but does not read as a headline.
            title = _announcement_title(body) or row["doc_title"] or headline
            item = Item(
                src=self.id, title=title, url=row["url"], date=row["date"],
                meta={
                    "src_kind": self.src_kind,
                    "venue": self.venue,
                    "region": "cn",
                    "stock_code": row["code"],
                    "stock_short_name": row["short_name"],
                    "companies": [company.name],
                    "company_tier": company.tier,
                    "document": row["doc_title"],
                    "headline_category": headline,
                    "body": clip("\n".join(filter(None, [title, body])), 40_000),
                    "quotable": clip("\n".join(filter(None, [title, body])), 40_000),
                },
            )
            if not ctx.store.is_seen(item.key):
                out.append(item)
        return out


#: An HKEX announcement opens with the exchange's standard disclaimer, then the
#: issuer's name, then the announcement's own title in capitals.
_DISCLAIMER_END = re.compile(r"contents of this announcement\.?\s*", re.I)


def _announcement_title(body: str) -> str:
    """The announcement's own title, from inside the PDF.

    Only an *announcement* carries one. The disclaimer is what says the
    document is one, so without it this reads nothing: Brii Biosciences'
    2026 Interim Report has no disclaimer, and scanning it for the first
    capitalised block returned its table-of-contents heading -- the item
    shipped titled "CONTENTS". The caller falls back to the exchange's own
    title for the document, which said "2026 Interim Report" all along.
    """
    if not body:
        return ""
    match = _DISCLAIMER_END.search(body)
    if not match:
        return ""
    tail = body[match.end():]
    caps: list[str] = []
    for line in tail.splitlines():
        line = line.strip()
        if not line:
            if caps:
                break
            continue
        letters = [c for c in line if c.isalpha()]
        if letters and sum(c.isupper() for c in letters) / len(letters) > 0.8:
            caps.append(line)
        elif caps:
            break
    title = " ".join(caps)
    # Every announcement opens with its own type; it is not part of the title.
    title = re.sub(r"^(?:VOLUNTARY ANNOUNCEMENT|OVERSEAS REGULATORY ANNOUNCEMENT|"
                   r"INSIDE INFORMATION(?: ANNOUNCEMENT)?|ANNOUNCEMENT)\s+", "",
                   title, flags=re.I)
    title = re.sub(r"^STOCK CODE:\s*\d+\s+", "", title, flags=re.I)
    return title.strip()[:250]
