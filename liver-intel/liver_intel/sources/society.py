"""Learned-society sources (EASL, AASLD).

A society is a first-party publisher in its own right: clinical practice
guidelines, congress abstracts and its own announcements. It is also where a
congress's dates and embargo times are stated, which is what T6 needs and what
no amount of recall can supply.

The two sites differ in what they expose, so the adapter takes either shape:
EASL runs WordPress and publishes a real feed; AASLD publishes none, so its
pages are watched for change. Both go through the registry and are used only
once verified.
"""
from __future__ import annotations

import logging

from ..models import Item
from .base import BaseSource, Context
from .regulator import _items_from_feed, _items_from_listwatch

log = logging.getLogger(__name__)


class SocietySource(BaseSource):
    id = "society"
    task = "T6"
    src_kind = "society"

    def collect(self, ctx: Context) -> list[Item]:
        out: list[Item] = []
        for feed in ctx.feeds(self.id):
            if feed.kind in ("rss", "atom"):
                out.extend(_items_from_feed(ctx, self, feed))
            else:
                out.extend(_items_from_listwatch(ctx, self, feed))
        for item in out:
            item.meta.setdefault("society", feed_society(item.meta.get("feed_id", "")))
        return self.emit(ctx, out)


def feed_society(feed_id: str) -> str:
    for marker, name in (("easl", "EASL"), ("aasld", "AASLD"), ("apasl", "APASL")):
        if marker in (feed_id or "").lower():
            return name.upper()
    return ""
