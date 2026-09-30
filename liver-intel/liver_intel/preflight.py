"""Host reachability check.

The first thing to run in a network-enabled session: it says, per host, whether
this machine can reach it at all -- before any feed logic is involved. A run
where every host reports ``blocked`` means the egress policy, not the code.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from .config import EXTRA_HOSTS
from .feeds import Registry
from .http import Blocked, Fetcher

OK = "ok"
BLOCKED = "blocked"
UNREACHABLE = "unreachable"
HTTP_ERROR = "http-error"


@dataclass
class HostCheck:
    host: str
    status: str
    detail: str = ""
    tasks: str = ""

    @property
    def reachable(self) -> bool:
        # An HTTP error still proves the connection got through; only a refused
        # tunnel or a dead connection means the host is out of reach.
        return self.status in (OK, HTTP_ERROR)


def hosts_from(registry: Registry) -> dict[str, tuple[str, set[str]]]:
    """{host: (probe url, tasks)} across the registry plus the extra API hosts."""
    out: dict[str, tuple[str, set[str]]] = {}
    for entry in registry.entries:
        host = urlparse(entry.url).netloc.lower()
        if not host:
            continue
        url, tasks = out.get(host, (entry.url, set()))
        if entry.task:
            tasks.add(entry.task)
        out[host] = (url, tasks)
    for host in EXTRA_HOSTS:
        out.setdefault(host, (f"https://{host}/", set()))
    return out


def check(registry: Registry, fetcher: Fetcher) -> list[HostCheck]:
    results: list[HostCheck] = []
    for host, (url, tasks) in sorted(hosts_from(registry).items()):
        label = ",".join(sorted(tasks))
        try:
            response = fetcher.get(url, allow_304=False)
        except Blocked as exc:
            results.append(HostCheck(host, BLOCKED, str(exc)[:120], label))
            continue
        except Exception as exc:
            results.append(HostCheck(host, UNREACHABLE, f"{type(exc).__name__}: "
                                     f"{str(exc)[:100]}", label))
            continue
        status = OK if response.ok else HTTP_ERROR
        results.append(HostCheck(host, status, f"HTTP {response.status}", label))
    return results


def summarise(results: list[HostCheck]) -> str:
    reachable = sum(1 for r in results if r.reachable)
    total = len(results)
    if reachable == 0:
        return (f"0/{total} hosts reachable -- this is the egress policy, not the "
                f"engine. Allow these hosts for the environment, then start a new "
                f"session so the container picks up the new policy.")
    if reachable < total:
        missing = ", ".join(r.host for r in results if not r.reachable)
        return f"{reachable}/{total} hosts reachable; still out of reach: {missing}"
    return f"{reachable}/{total} hosts reachable -- clear to run discover and verify."
