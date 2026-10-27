"""T8 -- X alert layer (optional, gated off).

Design constraint from the handover: a post is never content.  It is only a
hint that a first-party document exists, so this adapter resolves the links in
a post and hands those URLs to the primary-source collectors; the post itself
never becomes an Item.

The adapter stays disabled until (a) T1-T5 are stable and (b) the operator has
checked what the current X API tier actually allows.  ``enabled`` defaults to
False and there is no credential handling here yet on purpose.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

from ..models import is_excluded_host
from .base import BaseSource, Context

log = logging.getLogger(__name__)

_URL = re.compile(r"https?://[^\s\"'<>]+")


class XAlertSource(BaseSource):
    id = "xalert"
    task = "T8"
    src_kind = "company"
    requires_feeds = False

    def __init__(self, enabled: bool = False, handles: Iterable[str] = ()):
        self.enabled = enabled
        self.handles = tuple(handles)

    def collect(self, ctx: Context):
        if not self.enabled:
            ctx.note("[xalert] disabled. Enable only after T1-T5 are stable and the "
                     "current X API tier and quota have been checked.")
            return []
        ctx.note("[xalert] no X client is configured; the alert layer collected nothing.")
        return []

    @staticmethod
    def primary_links(post_text: str) -> list[str]:
        """Links worth handing to a primary-source fetcher."""
        return [url for url in _URL.findall(post_text or "")
                if not is_excluded_host(url) and "x.com" not in url and "t.co" not in url]
