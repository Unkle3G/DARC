"""Polite HTTP with per-host rate limiting and conditional GET.

Rules encoded here:

* Every request carries a User-Agent with a contact address -- mandated by the
  SEC EDGAR access policy (T2) and harmless elsewhere.
* Per-host token bucket; EDGAR stays under its published 10 req/s ceiling.
* 429/5xx retry with exponential backoff.  403 is **not** retried: a block is
  reported so the operator can decide, which is what T4 asks for on the
  Chinese regulator sites.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlparse

import requests

from .config import RATE_LIMITS, USER_AGENT

log = logging.getLogger(__name__)


class Blocked(RuntimeError):
    """Host refused us (403/451) -- do not retry, surface to the operator."""


class FetchError(RuntimeError):
    pass


@dataclass
class Response:
    url: str
    status: int
    text: str
    headers: Mapping[str, str]
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def body_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8", "replace")).hexdigest()

    def json(self) -> Any:
        import json

        return json.loads(self.text)


class Fetcher:
    """Shared HTTP client.  One instance per run."""

    def __init__(self, user_agent: str = USER_AGENT,
                 rate_limits: Mapping[str, float] | None = None,
                 timeout: float = 30.0, max_retries: int = 4,
                 session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
        })
        self.rate_limits = dict(rate_limits or RATE_LIMITS)
        self.timeout = timeout
        self.max_retries = max_retries
        self._last_call: dict[str, float] = {}
        self.blocked_hosts: set[str] = set()

    # ---- throttling -----------------------------------------------------
    def _min_interval(self, host: str) -> float:
        rate = self.rate_limits.get(host) or self.rate_limits.get("__default__", 1.0)
        return 1.0 / max(rate, 0.01)

    def _throttle(self, host: str) -> None:
        gap = self._min_interval(host)
        previous = self._last_call.get(host)
        if previous is not None:
            wait = gap - (time.monotonic() - previous)
            if wait > 0:
                time.sleep(wait)
        self._last_call[host] = time.monotonic()

    # ---- request --------------------------------------------------------
    def get(self, url: str, *, headers: Mapping[str, str] | None = None,
            etag: str | None = None, last_modified: str | None = None,
            allow_304: bool = True) -> Response:
        host = urlparse(url).netloc.lower()
        if host in self.blocked_hosts:
            raise Blocked(f"{host} previously refused this session")

        request_headers: dict[str, str] = dict(headers or {})
        if allow_304 and etag:
            request_headers["If-None-Match"] = etag
        if allow_304 and last_modified:
            request_headers["If-Modified-Since"] = last_modified

        delay = 2.0
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle(host)
            try:
                resp = self.session.get(url, headers=request_headers,
                                        timeout=self.timeout)
            except requests.RequestException as exc:      # network-level
                last_error = exc
                log.warning("%s attempt %d failed: %s", url, attempt, exc)
                if attempt == self.max_retries:
                    break
                time.sleep(delay)
                delay *= 2
                continue

            if resp.status_code in (403, 451):
                self.blocked_hosts.add(host)
                raise Blocked(f"{url} returned {resp.status_code}")
            if resp.status_code == 304:
                return Response(url, 304, "", resp.headers, from_cache=True)
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                last_error = FetchError(f"{url} -> {resp.status_code}")
                if attempt == self.max_retries:
                    break
                retry_after = resp.headers.get("Retry-After")
                sleep_for = delay
                if retry_after and retry_after.isdigit():
                    sleep_for = max(delay, float(retry_after))
                log.warning("%s -> %s, backing off %.0fs", url, resp.status_code, sleep_for)
                time.sleep(sleep_for)
                delay *= 2
                continue

            return Response(url, resp.status_code, resp.text, resp.headers)

        raise FetchError(f"{url} failed after {self.max_retries} attempts: {last_error}")

    def head_or_get(self, url: str) -> Response:
        """Used by the verifier: some feed hosts answer HEAD with 405."""
        try:
            host = urlparse(url).netloc.lower()
            self._throttle(host)
            resp = self.session.head(url, timeout=self.timeout, allow_redirects=True)
            if resp.status_code < 400 and resp.status_code != 405:
                return Response(url, resp.status_code, "", resp.headers)
        except requests.RequestException:
            pass
        return self.get(url, allow_304=False)
