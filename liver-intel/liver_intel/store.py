"""SQLite state (task sheet section 4).

Four things have to survive between runs:

* ``nct_state``   -- last seen ClinicalTrials.gov status per NCT, so T3 can fire
  on *changes* instead of on every LastUpdatePostDate touch.
* ``seen_items``  -- everything already published, so a release does not show up
  twice when two adapters carry it.
* ``page_state``  -- ETag/Last-Modified and body hashes for list-page change
  detection (NMPA/CDE in T4, company newsrooms in T7).
* ``weekly_pool`` -- P2/P3 that missed the daily cap, drained every Friday.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import Item

SCHEMA = """
CREATE TABLE IF NOT EXISTS nct_state (
    nct_id              TEXT PRIMARY KEY,
    overall_status      TEXT,
    why_stopped         TEXT,
    results_first_posted TEXT,
    last_update_posted  TEXT,
    phase               TEXT,
    first_seen          TEXT NOT NULL,
    last_seen           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seen_items (
    key         TEXT PRIMARY KEY,
    src         TEXT NOT NULL,
    url         TEXT NOT NULL,
    title       TEXT,
    item_date   TEXT,
    priority    TEXT,
    first_seen  TEXT NOT NULL,
    published_on TEXT
);
CREATE INDEX IF NOT EXISTS seen_items_src_idx ON seen_items(src, item_date);

CREATE TABLE IF NOT EXISTS page_state (
    key           TEXT PRIMARY KEY,
    url           TEXT NOT NULL,
    body_hash     TEXT,
    etag          TEXT,
    last_modified TEXT,
    last_checked  TEXT,
    last_changed  TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    note          TEXT
);

CREATE TABLE IF NOT EXISTS weekly_pool (
    key        TEXT PRIMARY KEY,
    priority   TEXT NOT NULL,
    item_json  TEXT NOT NULL,
    created    TEXT NOT NULL,
    drained_on TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    started   TEXT NOT NULL,
    finished  TEXT,
    kind      TEXT,
    stats_json TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class NctState:
    nct_id: str
    overall_status: str | None
    why_stopped: str | None
    results_first_posted: str | None
    last_update_posted: str | None
    phase: str | None = None

    def diff(self, other: "NctState") -> dict[str, tuple[Any, Any]]:
        """Fields whose change is meaningful for T3.

        LastUpdatePostDate deliberately does not appear here: the whole point of
        the state table is that a registry edit which touches nothing we care
        about produces no item.
        """
        watched = ("overall_status", "results_first_posted", "why_stopped")
        changes = {}
        for name in watched:
            before, after = getattr(self, name), getattr(other, name)
            if (before or None) != (after or None):
                changes[name] = (before, after)
        return changes


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- NCT state ------------------------------------------------------
    def get_nct(self, nct_id: str) -> NctState | None:
        row = self.conn.execute(
            "SELECT * FROM nct_state WHERE nct_id = ?", (nct_id,)
        ).fetchone()
        if row is None:
            return None
        return NctState(
            nct_id=row["nct_id"],
            overall_status=row["overall_status"],
            why_stopped=row["why_stopped"],
            results_first_posted=row["results_first_posted"],
            last_update_posted=row["last_update_posted"],
            phase=row["phase"],
        )

    def put_nct(self, state: NctState) -> None:
        now = _now()
        self.conn.execute(
            """
            INSERT INTO nct_state (nct_id, overall_status, why_stopped,
                results_first_posted, last_update_posted, phase, first_seen, last_seen)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(nct_id) DO UPDATE SET
                overall_status=excluded.overall_status,
                why_stopped=excluded.why_stopped,
                results_first_posted=excluded.results_first_posted,
                last_update_posted=excluded.last_update_posted,
                phase=excluded.phase,
                last_seen=excluded.last_seen
            """,
            (state.nct_id, state.overall_status, state.why_stopped,
             state.results_first_posted, state.last_update_posted, state.phase,
             now, now),
        )
        self.conn.commit()

    def known_nct_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM nct_state").fetchone()[0]

    # ---- seen items -----------------------------------------------------
    def is_seen(self, key: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM seen_items WHERE key = ?", (key,)
        ).fetchone() is not None

    def mark_seen(self, item: Item, published_on: str | None = None) -> None:
        self.conn.execute(
            """
            INSERT INTO seen_items (key, src, url, title, item_date, priority,
                                    first_seen, published_on)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET
                priority=excluded.priority,
                published_on=COALESCE(excluded.published_on, seen_items.published_on)
            """,
            (item.key, item.src, item.url, item.title, item.date, item.P,
             _now(), published_on),
        )
        self.conn.commit()

    # ---- page / list change detection -----------------------------------
    def get_page(self, key: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM page_state WHERE key = ?", (key,)
        ).fetchone()

    def put_page(self, key: str, url: str, body_hash: str | None,
                 etag: str | None = None, last_modified: str | None = None,
                 changed: bool = False, note: str | None = None) -> None:
        now = _now()
        previous = self.get_page(key)
        last_changed = now if changed else (previous["last_changed"] if previous else None)
        self.conn.execute(
            """
            INSERT INTO page_state (key, url, body_hash, etag, last_modified,
                                    last_checked, last_changed, consecutive_failures, note)
            VALUES (?,?,?,?,?,?,?,0,?)
            ON CONFLICT(key) DO UPDATE SET
                url=excluded.url, body_hash=excluded.body_hash,
                etag=excluded.etag, last_modified=excluded.last_modified,
                last_checked=excluded.last_checked,
                last_changed=excluded.last_changed,
                consecutive_failures=0, note=excluded.note
            """,
            (key, url, body_hash, etag, last_modified, now, last_changed, note),
        )
        self.conn.commit()

    def record_page_failure(self, key: str, url: str, note: str) -> int:
        """Count consecutive failures so T4 can downgrade to a manual prompt."""
        self.conn.execute(
            """
            INSERT INTO page_state (key, url, last_checked, consecutive_failures, note)
            VALUES (?,?,?,1,?)
            ON CONFLICT(key) DO UPDATE SET
                last_checked=excluded.last_checked,
                consecutive_failures=page_state.consecutive_failures + 1,
                note=excluded.note
            """,
            (key, url, _now(), note),
        )
        self.conn.commit()
        row = self.get_page(key)
        return int(row["consecutive_failures"]) if row else 1

    # ---- weekly pool ----------------------------------------------------
    def push_weekly(self, item: Item) -> None:
        self.conn.execute(
            """
            INSERT INTO weekly_pool (key, priority, item_json, created)
            VALUES (?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET
                priority=excluded.priority, item_json=excluded.item_json
            """,
            (item.key, item.P or "P3", json.dumps(item.to_json(), ensure_ascii=False), _now()),
        )
        self.conn.commit()

    def drain_weekly(self, mark_drained_on: str | None = None) -> list[Item]:
        rows = self.conn.execute(
            "SELECT key, item_json FROM weekly_pool WHERE drained_on IS NULL"
            " ORDER BY priority, created"
        ).fetchall()
        items = [Item.from_json(json.loads(row["item_json"])) for row in rows]
        if mark_drained_on and rows:
            self.conn.executemany(
                "UPDATE weekly_pool SET drained_on = ? WHERE key = ?",
                [(mark_drained_on, row["key"]) for row in rows],
            )
            self.conn.commit()
        return items

    # ---- run bookkeeping ------------------------------------------------
    @contextmanager
    def run(self, kind: str) -> Iterator[dict[str, Any]]:
        cursor = self.conn.execute(
            "INSERT INTO runs (started, kind) VALUES (?,?)", (_now(), kind)
        )
        run_id = cursor.lastrowid
        self.conn.commit()
        stats: dict[str, Any] = {}
        try:
            yield stats
        finally:
            self.conn.execute(
                "UPDATE runs SET finished = ?, stats_json = ? WHERE id = ?",
                (_now(), json.dumps(stats, ensure_ascii=False), run_id),
            )
            self.conn.commit()
