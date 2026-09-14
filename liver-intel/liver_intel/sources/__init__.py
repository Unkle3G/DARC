"""Source adapter registry."""
from __future__ import annotations

from .base import BaseSource, Context, Source
from .ctgov import CtGovSource
from .edgar import EdgarSource
from .exchange import CninfoSource, HkexSource
from .newsroom import NewsroomSource
from .newswire import NewswireSource
from .pubmed import PubmedSource
from .regulator import CnRegulatorSource, EmaSource, FdaSource
from .xalert import XAlertSource

#: Collection order follows the handover's priority: T1 first, T8 last.
ALL_SOURCES = (
    NewswireSource,       # T1
    EdgarSource,          # T2
    CtGovSource,          # T3
    FdaSource,            # T4
    EmaSource,            # T4
    CnRegulatorSource,    # T4
    PubmedSource,         # T4 (peer-reviewed channel)
    HkexSource,           # T5
    CninfoSource,         # T5
    NewsroomSource,       # T7
    XAlertSource,         # T8 (disabled by default)
)


def build_sources(only: list[str] | None = None) -> list[BaseSource]:
    sources = [cls() for cls in ALL_SOURCES]
    if only:
        wanted = set(only)
        sources = [s for s in sources if s.id in wanted]
    return sources


__all__ = ["ALL_SOURCES", "BaseSource", "Context", "Source", "build_sources",
           "CtGovSource", "EdgarSource", "NewswireSource", "FdaSource", "EmaSource",
           "CnRegulatorSource", "HkexSource", "CninfoSource", "NewsroomSource",
           "PubmedSource", "XAlertSource"]
