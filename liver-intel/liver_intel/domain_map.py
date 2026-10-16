"""Loader for ``domain_map_liver_v3.md``.

The dictionary is authored as markdown with fenced ``json`` blocks under
``## lines`` / ``## companies`` / ``## kol`` / ``## weights``.  Only the block
structure matters, so the operator can rewrite the contents freely.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import AUX_LINES, DOMAIN_MAP, MAIN_LINES

_BLOCK = re.compile(
    r"^##\s+(?P<name>[\w-]+)\s*$.*?```json\s*(?P<body>.*?)```",
    re.MULTILINE | re.DOTALL,
)


class DomainMapError(ValueError):
    pass


@dataclass
class Line:
    id: str
    tier: str
    name_zh: str = ""
    name_en: str = ""
    terms_en: list[str] = field(default_factory=list)
    terms_zh: list[str] = field(default_factory=list)
    #: Ambiguous surface forms that only fire when a gate term is also present
    #: (the HCC disease-word threshold from section 3).
    gated_terms_en: list[str] = field(default_factory=list)
    gated_terms_zh: list[str] = field(default_factory=list)
    fallback_terms_zh: list[str] = field(default_factory=list)
    gate_en: list[str] = field(default_factory=list)
    gate_zh: list[str] = field(default_factory=list)
    weight: float | None = None
    note: str = ""

    @property
    def is_main(self) -> bool:
        return self.tier == "main"

    @property
    def label(self) -> str:
        return f"{self.id} {self.name_zh}".strip()


@dataclass
class Company:
    name: str
    ticker: str | None = None
    #: EDGAR CIK, pinned when the ticker lookup cannot find the company:
    #: company_tickers.json lists only currently-listed tickers, so an acquired
    #: or delisted filer drops out of it even though its filings remain.
    cik: int | None = None
    #: False when the company has been confirmed not to file with the SEC. A
    #: ticker on this roster is not proof that EDGAR can reach a company: a
    #: Euronext or SIX listing has a ticker and no SEC filings at all.
    sec_filer: bool = True
    market: str = ""
    tier: int = 3
    lines: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def aliases(self) -> list[str]:
        """Name plus the obvious surface forms, including the Chinese name when
        the roster stores ``English 中文`` in one field."""
        out = {self.name}
        stripped = re.sub(r"\s*\(.*?\)\s*", " ", self.name).strip()
        out.add(stripped)
        # split mixed "Brii Biosciences 腾盛博药" into both halves
        han = re.findall(r"[一-鿿]+", self.name)
        out.update(han)
        latin = " ".join(re.findall(r"[A-Za-z0-9&.\-]+", self.name)).strip()
        if latin:
            out.add(latin)
            for suffix in (" Pharmaceuticals", " Pharmaceutical", " Therapeutics",
                           " Biosciences", " Biopharma", " Biotechnology", " Pharma",
                           " Inc", " Inc.", " Ltd", " Limited", " Corp", " & Co"):
                if latin.endswith(suffix):
                    out.add(latin[: -len(suffix)].strip())
        return sorted({a for a in out if len(a) >= 3})

    @property
    def plain_ticker(self) -> str | None:
        if not self.ticker:
            return None
        return self.ticker.split(".")[0]


@dataclass
class Kol:
    name: str
    lines: list[str] = field(default_factory=list)
    region: str = ""
    verified: bool = False
    note: str = ""


@dataclass
class DomainMap:
    lines: dict[str, Line]
    companies: list[Company]
    kols: list[Kol]
    weights: dict[str, Any]
    path: Path

    # -- lookups ---------------------------------------------------------
    def line(self, line_id: str) -> Line | None:
        return self.lines.get(line_id)

    def line_weight(self, line_id: str) -> float:
        line = self.lines.get(line_id)
        if line is None:
            return 0.0
        if line.weight is not None:
            return float(line.weight)
        exceptions = self.weights.get("line_exceptions", {})
        if line_id in exceptions:
            return float(exceptions[line_id])
        key = "line_main" if line.is_main else "line_aux"
        return float(self.weights.get(key, 1.0 if line.is_main else 0.5))

    def main_lines(self) -> list[str]:
        return [lid for lid, line in self.lines.items() if line.is_main]

    @property
    def verified_kols(self) -> list[Kol]:
        return [k for k in self.kols if k.verified]

    @property
    def unverified_kols(self) -> list[Kol]:
        return [k for k in self.kols if not k.verified]

    def companies_for_market(self, *markets: str) -> list[Company]:
        wanted = set(markets)
        return [c for c in self.companies if c.market in wanted]

    def company_tier_weight(self, tier: int) -> float:
        table = self.weights.get("company_tier", {})
        return float(table.get(str(tier), table.get(tier, 0.4)))


def _blocks(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for match in _BLOCK.finditer(text):
        name = match.group("name").lower()
        try:
            out[name] = json.loads(match.group("body"))
        except json.JSONDecodeError as exc:
            raise DomainMapError(f"section '{name}' is not valid JSON: {exc}") from exc
    return out


def load(path: Path | str | None = None) -> DomainMap:
    path = Path(path or DOMAIN_MAP)
    if not path.exists():
        raise DomainMapError(f"domain map not found at {path}")
    data = _blocks(path.read_text(encoding="utf-8"))
    for required in ("lines", "companies", "kol", "weights"):
        if required not in data:
            raise DomainMapError(f"domain map is missing the '## {required}' block")

    lines: dict[str, Line] = {}
    for raw in data["lines"]:
        known = {k: v for k, v in raw.items() if k in Line.__dataclass_fields__}
        line = Line(**known)
        lines[line.id] = line

    expected = set(MAIN_LINES) | set(AUX_LINES)
    missing = expected - set(lines)
    if missing:
        raise DomainMapError(f"domain map is missing lines: {sorted(missing)}")
    for line_id, line in lines.items():
        should_be_main = line_id in MAIN_LINES
        if should_be_main != line.is_main:
            raise DomainMapError(
                f"{line_id} is declared tier={line.tier!r} but config puts it in "
                f"{'main' if should_be_main else 'aux'}"
            )

    companies = [
        Company(**{k: v for k, v in raw.items() if k in Company.__dataclass_fields__})
        for raw in data["companies"]
    ]
    kols = [
        Kol(**{k: v for k, v in raw.items() if k in Kol.__dataclass_fields__})
        for raw in data["kol"]
    ]
    return DomainMap(lines=lines, companies=companies, kols=kols,
                     weights=data["weights"], path=path)


@lru_cache(maxsize=4)
def load_cached(path: str | None = None) -> DomainMap:
    return load(path)
