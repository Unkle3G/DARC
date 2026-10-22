"""T5 -- HKEX (披露易) and CNINFO (巨潮) statutory announcements.

Both venues expose announcement search endpoints; which one, and with what
parameters, is a registry entry that has to be verified before use.  The
adapter keeps the announcement-type filter the handover asks for: voluntary
announcements, clinical-trial updates and drug-registration filings.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..models import Item, as_iso_date
from ..textutil import clip
from .base import BaseSource, Context

log = logging.getLogger(__name__)

#: Title keywords that keep a filing in scope.  Chinese issuers title
#: announcements consistently, so this is a title filter, not a judgement.
KEEP_TITLE_TERMS = (
    "自願公告", "自愿公告", "臨床試驗", "临床试验", "藥品註冊", "药品注册",
    "藥物", "药物", "新藥", "新药", "獲批", "获批", "受理", "上市申請", "上市申请",
    "III期", "Ⅲ期", "關鍵性", "关键性", "突破性治療", "突破性治疗",
    "voluntary announcement", "clinical trial", "drug registration",
)


class ExchangeSource(BaseSource):
    """Shared implementation; HKEX and CNINFO differ only in registry entries."""

    id = "hkex"
    task = "T5"
    src_kind = "filing"
    venue = "HKEX"

    def collect(self, ctx: Context) -> list[Item]:
        out: list[Item] = []
        roster = {c.name: c for c in ctx.domain_map.companies_for_market("hk", "cn")}
        for feed in ctx.feeds(self.id):
            try:
                response = ctx.fetcher.get(feed.url, allow_304=False)
            except Exception as exc:
                ctx.note(f"[{self.id}] {feed.id} unreadable: {exc}")
                continue
            if not response.ok:
                ctx.note(f"[{self.id}] {feed.id} HTTP {response.status}")
                continue
            try:
                payload = response.json()
            except json.JSONDecodeError:
                ctx.note(f"[{self.id}] {feed.id} did not return JSON; "
                         "re-verify the endpoint shape")
                continue
            for record in _records(payload):
                item = self._to_item(ctx, feed, record, roster)
                if item is not None:
                    out.append(item)
        return self.emit(ctx, out)

    def _to_item(self, ctx: Context, feed: Any, record: dict[str, Any],
                 roster: dict[str, Any]) -> Item | None:
        title = str(record.get("title") or record.get("announcementTitle") or "").strip()
        url = str(record.get("url") or record.get("adjunctUrl") or "").strip()
        if not title or not url:
            return None
        if url.startswith("/"):
            url = f"https://{feed.url.split('/')[2]}{url}"
        if not any(term.lower() in title.lower() for term in KEEP_TITLE_TERMS):
            return None
        date = as_iso_date(record.get("date") or record.get("announcementTime")
                           or record.get("dateTime"))
        if not date:
            return None
        if ctx.since and date < ctx.since:
            return None

        company_name = str(record.get("company") or record.get("secName")
                           or feed.company or "").strip()
        company = roster.get(company_name)
        item = Item(
            src=self.id, title=title, url=url, date=date,
            meta={
                "src_kind": self.src_kind,
                "venue": self.venue,
                "feed_id": feed.id,
                "region": "cn",
                "body": clip(title, 2000),
            },
        )
        if company_name:
            item.meta["companies"] = [company_name]
        if company is not None:
            item.meta["company_tier"] = company.tier
        if ctx.store.is_seen(item.key):
            return None
        return item


class CninfoSource(ExchangeSource):
    id = "cninfo"
    venue = "CNINFO"


def _records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("announcements", "result", "results", "data", "records", "rows"):
            value = payload.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
    return []
