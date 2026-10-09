"""The state branch is the only copy of the run state; a push that silently
keeps the old archive is indistinguishable from a good one until the day
someone restores from it.

The first version of ``state_sync.sh push`` gzipped the database into the same
directory it then turned into a scratch repo, so ``git reset --hard
FETCH_HEAD`` overwrote the fresh archive with the previous commit's copy. Four
pushes in a row re-committed the first archive; the branch carried a pre-004
database labelled "issue 5", and restoring from it lost two days of dedup.
Hence the test below: push twice, with a different database the second time,
and read the archive back out of the branch.
"""
from __future__ import annotations

import gzip
import sqlite3
import subprocess
from pathlib import Path

import pytest

from liver_intel.store import Store

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "state_sync.sh"


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def _seed(db_path: Path, keys: list[str]) -> None:
    """A real database, written through the real schema."""
    with Store(db_path) as store:
        for key in keys:
            store.conn.execute(
                "INSERT OR REPLACE INTO seen_items"
                " (key, src, url, title, item_date, priority, first_seen)"
                " VALUES (?,?,?,?,?,?,?)",
                (key, "pubmed", f"https://example.test/{key}", key,
                 "2026-09-24", "P2", "2026-09-24T00:00:00+00:00"))
        store.conn.commit()


def _run(project: Path, verb: str, env_extra: dict[str, str] | None = None):
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(project),
           "LIVER_INTEL_STATE_BRANCH": "test-state"}
    env.update(env_extra or {})
    return subprocess.run(["bash", str(project / "scripts" / "state_sync.sh"), verb],
                          cwd=project, capture_output=True, text=True, env=env)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git("init", "-q", "--bare", cwd=origin)

    project = tmp_path / "liver-intel"
    (project / "scripts").mkdir(parents=True)
    (project / "data").mkdir()
    (project / "out").mkdir()
    (project / "scripts" / "state_sync.sh").write_bytes(SCRIPT.read_bytes())
    (project / "scripts" / "state_sync.sh").chmod(0o755)

    _git("init", "-q", cwd=project)
    _git("remote", "add", "origin", str(origin), cwd=project)
    return project


def _committed_keys(project: Path) -> set[str]:
    """The seen_items the branch actually holds, read the long way round."""
    _git("fetch", "-q", "origin", "test-state", cwd=project)
    blob = subprocess.run(["git", "show", "FETCH_HEAD:state.sqlite3.gz"],
                          cwd=project, check=True, capture_output=True).stdout
    restored = project / "restored.sqlite3"
    restored.write_bytes(gzip.decompress(blob))
    db = sqlite3.connect(restored)
    try:
        return {row[0] for row in db.execute("SELECT key FROM seen_items")}
    finally:
        db.close()
        restored.unlink()


def test_second_push_commits_the_second_database(project: Path) -> None:
    db = project / "data" / "state.sqlite3"
    _seed(db, ["aaa"])
    first = _run(project, "push")
    assert first.returncode == 0, first.stderr
    assert _committed_keys(project) == {"aaa"}

    # The state moves on, as it does every day.
    _seed(db, ["aaa", "bbb"])
    second = _run(project, "push")
    assert second.returncode == 0, second.stderr
    # The bug shipped a branch that still said {"aaa"} here.
    assert _committed_keys(project) == {"aaa", "bbb"}
    assert "pushed" in second.stdout


def test_push_reports_the_counts_it_committed(project: Path) -> None:
    _seed(project / "data" / "state.sqlite3", ["aaa", "bbb", "ccc"])
    out = _run(project, "push")
    assert out.returncode == 0, out.stderr
    assert "committed holds 3 seen items" in out.stdout


def test_pull_keeps_the_database_it_replaces(project: Path) -> None:
    db = project / "data" / "state.sqlite3"
    _seed(db, ["aaa"])
    assert _run(project, "push").returncode == 0

    # A newer local database, as on the day the pull threw one away.
    _seed(db, ["aaa", "newer"])
    assert _run(project, "pull").returncode == 0
    kept = db.with_suffix(".sqlite3.replaced")
    assert kept.exists(), "a pull must not be the only thing standing between\
 the newer state and nothing"
    db_kept = sqlite3.connect(kept)
    try:
        assert {r[0] for r in db_kept.execute("SELECT key FROM seen_items")} == {"aaa", "newer"}
    finally:
        db_kept.close()
