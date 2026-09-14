"""The item contract (task sheet section 2).

Every collected record is one :class:`Item`.  JSON field order is fixed so
that diffs between runs stay readable:

    src, title, url, date, meta, lines, study, P, why, score, evidence

``evidence`` is written by the LLM significance step and carries
``signals[]`` plus ``quotes[]`` -- hit signals and the source text that
supports them, never a conclusion.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable
from urllib.parse import urlparse

PRIORITIES = ("P0", "P1", "P2", "P3")

#: Source classes we accept.  Section 0: company announcements, regulators,
#: trial registries, peer-reviewed journals.  Nothing else may produce an Item.
PRIMARY_SRC_KINDS = {
    "company",        # issuer newsroom / newswire release / exchange filing
    "regulator",      # FDA, EMA, NMPA, CDE, PMDA ...
    "registry",       # ClinicalTrials.gov, ChiCTR, EU CTIS
    "journal",        # peer-reviewed, published (not ahead-of-print preprint)
    "filing",         # SEC / HKEX / CNINFO statutory filing
    "conference",     # society-issued abstract / late-breaker book
}

#: Hosts that are second-hand reporting or preprints.  Section 0 forbids both;
#: section 3 additionally records that Endpoints blocks us with 403 anyway.
EXCLUDED_HOST_SUBSTRINGS = (
    "endpts.com", "endpoints.news", "fiercebiotech.com", "fiercepharma.com",
    "statnews.com", "reuters.com", "bloomberg.com", "cnbc.com",
    "biopharmadive.com", "pharmavoice.com", "medscape.com", "yicai.com",
    "sina.com", "36kr.com", "xueqiu.com",
    "medrxiv.org", "biorxiv.org", "researchsquare.com", "ssrn.com",
    "papers.ssrn.com", "preprints.org", "chinaxiv.org",
)


class ContractError(ValueError):
    """Raised when an item violates the output contract."""


@dataclass
class Quote:
    """A verbatim span lifted from the source document.

    ``text`` must occur in the fetched source text -- :func:`validate_quotes`
    enforces this, which is what stops the LLM step from inventing support.
    """

    text: str
    url: str = ""
    locator: str = ""          # e.g. "EX-99.1 para 3", "whyStopped"
    lang: str = ""             # source language of ``text``: "zh", "en", ...
    #: Chinese rendering of a non-Chinese quote. Empty for Chinese sources, and
    #: empty when no model ran -- the engine never invents a translation.
    translation: str = ""

    def to_json(self) -> dict[str, Any]:
        out = {"text": self.text}
        for key in ("url", "locator", "lang", "translation"):
            value = getattr(self, key)
            if value:
                out[key] = value
        return out


@dataclass
class Evidence:
    """LLM significance output: which signals fired and the text behind them."""

    signals: list[str] = field(default_factory=list)
    quotes: list[Quote] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "signals": list(self.signals),
            "quotes": [q.to_json() for q in self.quotes],
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any] | None) -> "Evidence":
        raw = raw or {}
        quotes = []
        for q in raw.get("quotes", []) or []:
            if isinstance(q, str):
                quotes.append(Quote(text=q))
            else:
                quotes.append(Quote(**{k: v for k, v in q.items()
                                       if k in Quote.__dataclass_fields__}))
        return cls(signals=list(raw.get("signals", []) or []), quotes=quotes)


@dataclass
class Item:
    """One intelligence record."""

    src: str                              # adapter id, e.g. "edgar", "ctgov"
    title: str
    url: str                              # original source URL, always present
    date: str                             # ISO date used for archiving
    meta: dict[str, Any] = field(default_factory=dict)
    lines: list[str] = field(default_factory=list)
    study: list[str] = field(default_factory=list)
    P: str | None = None
    why: str = ""
    score: float = 0.0
    evidence: Evidence = field(default_factory=Evidence)

    # ---- identity -------------------------------------------------------
    @property
    def key(self) -> str:
        """Stable dedup key.  URL identifies the document; the source id keeps
        the same document arriving through two adapters from colliding."""
        basis = f"{self.src}|{canonical_url(self.url)}"
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]

    @property
    def src_kind(self) -> str:
        return str(self.meta.get("src_kind", ""))

    # ---- serialisation --------------------------------------------------
    def to_json(self) -> dict[str, Any]:
        return {
            "src": self.src,
            "title": self.title,
            "url": self.url,
            "date": self.date,
            "meta": self.meta,
            "lines": list(self.lines),
            "study": list(self.study),
            "P": self.P,
            "why": self.why,
            "score": round(float(self.score), 4),
            "evidence": self.evidence.to_json(),
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Item":
        return cls(
            src=raw["src"],
            title=raw["title"],
            url=raw["url"],
            date=raw["date"],
            meta=dict(raw.get("meta") or {}),
            lines=list(raw.get("lines") or []),
            study=list(raw.get("study") or []),
            P=raw.get("P"),
            why=raw.get("why", ""),
            score=float(raw.get("score") or 0.0),
            evidence=Evidence.from_json(raw.get("evidence")),
        )


def canonical_url(url: str) -> str:
    """Strip tracking noise so the same release seen twice dedups."""
    parsed = urlparse(url.strip())
    query = "&".join(
        part for part in parsed.query.split("&")
        if part and not part.split("=")[0].lower().startswith(("utm_", "src", "mkt_tok"))
    )
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme.lower()}://{netloc}{path}" + (f"?{query}" if query else "")


def is_excluded_host(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(bad in host for bad in EXCLUDED_HOST_SUBSTRINGS)


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate(item: Item) -> Item:
    """Enforce section 0 and section 2 invariants.  Raises ContractError."""
    if not item.title.strip():
        raise ContractError("title is empty")
    if not item.url.strip():
        raise ContractError(f"item {item.title!r} carries no source URL")
    scheme = urlparse(item.url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ContractError(f"non-http source URL: {item.url!r}")
    if is_excluded_host(item.url):
        raise ContractError(
            f"{item.url} is a media relay or preprint server; only first-party "
            "sources are allowed"
        )
    kind = item.meta.get("src_kind")
    if kind not in PRIMARY_SRC_KINDS:
        raise ContractError(
            f"meta.src_kind={kind!r} is not one of {sorted(PRIMARY_SRC_KINDS)}"
        )
    if not _ISO_DATE.match(item.date or ""):
        raise ContractError(f"date must be ISO YYYY-MM-DD, got {item.date!r}")
    if item.P is not None and item.P not in PRIORITIES:
        raise ContractError(f"unknown priority {item.P!r}")
    return item


def validate_quotes(item: Item, source_text: str) -> list[Quote]:
    """Return quotes that do **not** appear verbatim in ``source_text``.

    The significance step must cite the document; anything it cannot be found
    in is dropped by the pipeline rather than published.
    """
    haystack = _normalise_ws(source_text)
    missing = []
    for quote in item.evidence.quotes:
        if _normalise_ws(quote.text) not in haystack:
            missing.append(quote)
    return missing


def _normalise_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def is_chinese(text: str) -> bool:
    """True when the span is predominantly Chinese.

    Used to decide whether a quote needs a translation for a Chinese-reading
    audience; a stray Chinese character in an English sentence must not count.
    """
    stripped = re.sub(r"\s", "", text or "")
    if not stripped:
        return False
    return len(_CJK.findall(stripped)) / len(stripped) >= 0.2


def today_iso(tz: str | None = None) -> str:
    if tz:
        try:
            from zoneinfo import ZoneInfo

            return datetime.now(ZoneInfo(tz)).date().isoformat()
        except Exception:  # pragma: no cover - zoneinfo data missing
            pass
    return date.today().isoformat()


def as_iso_date(value: Any) -> str | None:
    """Best-effort ISO date from the shapes feeds actually emit."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if _ISO_DATE.match(text):
        return text
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    if re.match(r"^\d{4}-\d{2}$", text):
        text += "-01"
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
                "%Y/%m/%d", "%Y%m%d", "%d %b %Y", "%b %d, %Y", "%B %d, %Y",
                "%Y\u5e74%m\u6708%d\u65e5"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(text).date().isoformat()
    except Exception:
        return None


def dedup(items: Iterable[Item]) -> list[Item]:
    """Drop repeats, keeping the first occurrence."""
    seen: set[str] = set()
    out: list[Item] = []
    for item in items:
        if item.key in seen:
            continue
        seen.add(item.key)
        out.append(item)
    return out
