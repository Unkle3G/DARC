"""Per-company source coverage.

"Is coverage thin?" is not a feeling to argue about -- it is a table. For each
company on the roster this reports which sources can actually reach it, so a gap
is a named company with a named missing source rather than a general worry.

Sources fall into two kinds, and only the first is coverage of a *company*:

* **Company-specific** -- the source is addressed by company: an EDGAR CIK, an
  exchange listing, that company's own newsroom feed. If one of these is
  missing, the company's own announcements can be missed entirely.
* **Cross-cutting** -- the source is searched by subject and happens to surface
  the company: the trial registry, the literature, the regulator's database,
  a wire's category feed. These catch events regardless of who announces them,
  which is why a company with no company-specific source is not invisible, only
  dependent on someone else publishing first.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .domain_map import Company, DomainMap
from .feeds import Registry


@dataclass
class CompanyCoverage:
    company: Company
    specific: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def covered(self) -> bool:
        return bool(self.specific)


@dataclass
class Coverage:
    companies: list[CompanyCoverage]
    cross_cutting: list[str]

    @property
    def rate(self) -> float:
        if not self.companies:
            return 0.0
        return sum(1 for c in self.companies if c.covered) / len(self.companies)

    @property
    def uncovered(self) -> list[CompanyCoverage]:
        return [c for c in self.companies if not c.covered]


def _verified(registry: Registry, *feed_ids: str) -> bool:
    return all((registry.by_id(fid) or None) and registry.by_id(fid).is_usable
               for fid in feed_ids)


def assess(dm: DomainMap, registry: Registry) -> Coverage:
    edgar_ready = _verified(registry, "sec.submissions", "sec.company_tickers")
    hkex_ready = bool(registry.usable(source="hkex"))
    cninfo_ready = bool(registry.usable(source="cninfo"))
    newsroom_companies = {f.company for f in registry.usable(source="newsroom") if f.company}
    newswire_companies = {f.company for f in registry.usable(source="newswire") if f.company}

    cross: list[str] = []
    if registry.by_id("ctgov.v2") and registry.by_id("ctgov.v2").is_usable:
        cross.append("trial registry (ClinicalTrials.gov)")
    if registry.by_id("pubmed.esummary") and registry.by_id("pubmed.esummary").is_usable:
        cross.append("literature (PubMed)")
    if registry.by_id("openfda.drugsfda") and registry.by_id("openfda.drugsfda").is_usable:
        cross.append("regulator (openFDA)")
    broad = [f for f in registry.usable(source="newswire") if not f.company]
    if broad:
        cross.append(f"wire category feeds ({len(broad)})")

    out: list[CompanyCoverage] = []
    for company in dm.companies:
        entry = CompanyCoverage(company=company)
        if company.name in newsroom_companies:
            entry.specific.append("newsroom")
        if company.name in newswire_companies:
            entry.specific.append("wire (per company)")

        if company.market not in ("hk", "cn") and company.sec_filer:
            if edgar_ready and (company.plain_ticker or company.cik):
                # 8-K for a domestic filer, 6-K for a foreign private issuer.
                entry.specific.append("EDGAR")
            elif not edgar_ready:
                entry.missing.append("EDGAR endpoints unverified")
            else:
                entry.missing.append("no ticker or CIK on the roster "
                                     "(not a SEC filer, or needs one pinned)")
        elif not company.sec_filer:
            entry.missing.append("confirmed not a SEC filer: needs its own newsroom")
        elif company.market == "hk":
            (entry.specific if hkex_ready else entry.missing).append(
                "HKEX" if hkex_ready else "HKEX unverified")
        elif company.market == "cn":
            (entry.specific if cninfo_ready else entry.missing).append(
                "CNINFO" if cninfo_ready else "CNINFO unreachable")
        else:
            entry.missing.append("no exchange or filing source for this market")

        if not entry.specific:
            entry.missing.append("no newsroom feed registered")
        out.append(entry)
    return Coverage(companies=out, cross_cutting=cross)
