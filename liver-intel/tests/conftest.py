import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from liver_intel.config import Settings
from liver_intel.domain_map import load as load_domain_map
from liver_intel.http import Response
from liver_intel.store import Store


class FakeFetcher:
    """Serves canned responses; fails loudly on an unexpected URL."""

    def __init__(self, routes: dict[str, object] | None = None):
        self.routes = dict(routes or {})
        self.calls: list[str] = []
        self.blocked_hosts: set[str] = set()

    def add(self, url: str, body: str, status: int = 200,
            headers: dict[str, str] | None = None) -> None:
        self.routes[url] = (status, body, headers or {})

    def get(self, url, headers=None, etag=None, last_modified=None, allow_304=True):
        self.calls.append(url)
        route = self.routes.get(url)
        if route is None:
            for prefix, value in self.routes.items():
                if url.startswith(prefix):
                    route = value
                    break
        if route is None:
            raise RuntimeError(f"unexpected fetch: {url}")
        if isinstance(route, Exception):
            raise route
        status, body, response_headers = route
        return Response(url, status, body, response_headers)

    head_or_get = get


@pytest.fixture
def fake_fetcher():
    return FakeFetcher()


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "state.sqlite3") as db:
        yield db


@pytest.fixture
def domain_map():
    return load_domain_map()


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, out_dir=tmp_path / "out",
                    db_path=tmp_path / "state.sqlite3")
