"""Adapter plumbing shared by every source."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from ..config import Settings
from ..domain_map import DomainMap
from ..feeds import Feed, Registry
from ..http import Blocked, Fetcher
from ..models import Item, ContractError, validate
from ..store import Store

log = logging.getLogger(__name__)


@dataclass
class Context:
    settings: Settings
    fetcher: Fetcher
    store: Store
    registry: Registry
    domain_map: DomainMap
    today: str
    since: str | None = None          # ISO date, for backfills
    conference_window: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)
    #: Remaining budget for expensive per-document fetches (HKEX PDFs).
    pdf_budget: int = 0

    def feeds(self, source: str) -> list[Feed]:
        return self.registry.usable(
            source=source, allow_unverified=self.settings.allow_unverified)

    def note(self, message: str) -> None:
        log.info(message)
        self.notes.append(message)


class Source(Protocol):
    id: str
    task: str

    def collect(self, ctx: Context) -> list[Item]: ...


class BaseSource:
    """Common behaviour: contract validation, blocked-host handling, logging."""

    id: str = "base"
    task: str = ""
    src_kind: str = "company"

    def collect(self, ctx: Context) -> list[Item]:  # pragma: no cover - interface
        raise NotImplementedError

    # -- helpers ---------------------------------------------------------
    def emit(self, ctx: Context, items: Iterable[Item]) -> list[Item]:
        """Validate and drop anything that breaks the contract, loudly."""
        out: list[Item] = []
        for item in items:
            item.meta.setdefault("src_kind", self.src_kind)
            item.meta.setdefault("task", self.task)
            try:
                out.append(validate(item))
            except ContractError as exc:
                ctx.note(f"[{self.id}] dropped an item: {exc}")
        return out

    def run(self, ctx: Context) -> list[Item]:
        """Collect with blocked-host and failure handling so one dead source
        never takes the daily run down with it."""
        if not ctx.feeds(self.id) and self.requires_feeds:
            ctx.note(
                f"[{self.id}] no verified endpoint in the registry -- skipped. "
                f"Run `liver-intel discover --source {self.id}` then "
                f"`liver-intel verify --source {self.id}`.")
            return []
        try:
            return self.collect(ctx)
        except Blocked as exc:
            ctx.note(f"[{self.id}] blocked: {exc}. Not retried; handle manually.")
            return []
        except Exception as exc:  # noqa: BLE001 - one bad source must not stop the run
            log.exception("[%s] collection failed", self.id)
            ctx.note(f"[{self.id}] failed: {exc}")
            return []

    #: Sources that build their URLs from a documented API base still read the
    #: registry; sources that need no endpoint at all set this False.
    requires_feeds: bool = True
