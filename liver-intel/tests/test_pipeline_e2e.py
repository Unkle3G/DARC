"""End-to-end: a canned wire feed and an EDGAR filing become a daily report."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from liver_intel import pipeline
from liver_intel.feeds import Feed, Registry
from liver_intel.llm import NullJudge
from liver_intel.sources.base import Context

WIRE_FEED = """<rss><channel>
<item><title>Madrigal Announces Phase 3 Topline Results in MASH</title>
<link>https://www.globenewswire.com/news-release/2026/09/14/a.html</link>
<pubDate>Mon, 14 Sep 2026 08:00:00 GMT</pubDate>
<description>Topline results from the Phase 3 trial</description></item>
<item><title>Ascletis reports hepatitis C cohort data</title>
<link>https://www.globenewswire.com/news-release/2026/09/14/b.html</link>
<pubDate>Mon, 14 Sep 2026 07:00:00 GMT</pubDate>
<description>Phase 2 hepatitis C data</description></item>
<item><title>Vendor conference schedule update</title>
<link>https://www.globenewswire.com/news-release/2026/09/14/c.html</link>
<pubDate>Mon, 14 Sep 2026 06:00:00 GMT</pubDate>
<description>Corporate calendar</description></item>
</channel></rss>"""

RELEASE_A = """<html><body><p>Madrigal Pharmaceuticals today announced that the
Phase 3 MAESTRO-NASH trial met the primary endpoint of MASH resolution on liver
biopsy in patients with F2-F3 fibrosis.</p></body></html>"""

RELEASE_B = """<html><body><p>Ascletis reported Phase 2 results in chronic
hepatitis C with sustained virologic response.</p></body></html>"""

RELEASE_C = """<html><body><p>The company will attend an investor conference.</p></body></html>"""


@pytest.fixture
def wired(tmp_path, fake_fetcher, domain_map, monkeypatch):
    settings = replace(
        pipeline.Settings(), out_dir=tmp_path / "out", db_path=tmp_path / "state.sqlite3")
    registry = Registry(entries=[Feed(id="wire.liver", url="https://wire.example.com/rss",
                                      source="newswire", task="T1", status="verified")])
    fake_fetcher.add("https://wire.example.com/rss", WIRE_FEED)
    fake_fetcher.add("https://www.globenewswire.com/news-release/2026/09/14/a.html", RELEASE_A)
    fake_fetcher.add("https://www.globenewswire.com/news-release/2026/09/14/b.html", RELEASE_B)
    fake_fetcher.add("https://www.globenewswire.com/news-release/2026/09/14/c.html", RELEASE_C)

    def build_context(settings_, store, today, since=None, domain_map_=None):
        return Context(settings=settings_, fetcher=fake_fetcher, store=store,
                       registry=registry, domain_map=domain_map, today=today, since=since)

    monkeypatch.setattr(pipeline, "build_context",
                        lambda s, store, today, since=None, domain_map=None:
                        build_context(s, store, today, since))
    monkeypatch.setattr(pipeline.llm, "build_judge", lambda enabled=True: NullJudge())
    return settings


def test_daily_run_produces_a_graded_report(wired):
    result = pipeline.run_daily(wired, today="2026-09-14", only=["newswire"])

    assert result.collected == 3
    titles = [i.title for i in result.daily]
    assert "Madrigal Announces Phase 3 Topline Results in MASH" in titles

    madrigal = next(i for i in result.daily if i.title.startswith("Madrigal"))
    assert madrigal.P == "P0"
    assert madrigal.lines == ["L3"]
    assert "PH3_RESULT" in madrigal.evidence.signals
    assert madrigal.meta["companies"] == ["Madrigal Pharmaceuticals"]

    # hepatitis C only -> auxiliary line -> weekly digest, never the daily report
    assert all("Ascletis" not in i.title for i in result.daily)
    assert any("Ascletis" in i.title for i in result.weekly)

    body = result.report_path.read_text(encoding="utf-8")
    assert "# 肝病情报日报 2026-09-14" in body
    assert madrigal.url in body
    assert "L3" in body

    payload = json.loads(result.json_path.read_text(encoding="utf-8"))
    assert list(payload[0]) == ["src", "title", "url", "date", "meta", "lines",
                               "study", "P", "why", "score", "evidence"]


def test_second_run_does_not_repeat_published_items(wired):
    first = pipeline.run_daily(wired, today="2026-09-14", only=["newswire"])
    assert first.daily
    second = pipeline.run_daily(wired, today="2026-09-15", only=["newswire"])
    assert second.collected == 0
    assert second.daily == []


def test_weekly_digest_drains_the_pool(wired):
    pipeline.run_daily(wired, today="2026-09-14", only=["newswire"])
    weekly = pipeline.run_weekly(wired, today="2026-09-18")
    assert weekly.weekly, "auxiliary-line items should have reached the pool"
    body = weekly.report_path.read_text(encoding="utf-8")
    assert "肝病情报周汇总" in body
    # draining is idempotent: a second Friday run does not re-issue the same items
    again = pipeline.run_weekly(wired, today="2026-09-25")
    assert again.weekly == []


def test_run_without_verified_feeds_collects_nothing(tmp_path, fake_fetcher,
                                                     domain_map, monkeypatch):
    settings = replace(pipeline.Settings(), out_dir=tmp_path / "out",
                       db_path=tmp_path / "state.sqlite3")
    empty = Registry(entries=[Feed(id="wire.liver", url="https://wire.example.com/rss",
                                   source="newswire", task="T1", status="unverified")])
    monkeypatch.setattr(pipeline, "build_context",
                        lambda s, store, today, since=None, domain_map_=None:
                        Context(settings=s, fetcher=fake_fetcher, store=store,
                                registry=empty, domain_map=domain_map, today=today))
    monkeypatch.setattr(pipeline.llm, "build_judge", lambda enabled=True: NullJudge())
    result = pipeline.run_daily(settings, today="2026-09-14", only=["newswire"])
    assert result.collected == 0
    assert fake_fetcher.calls == []
    assert "无条目" in result.report_path.read_text(encoding="utf-8")


def test_rule_signals_carry_verbatim_source_quotes(wired):
    from liver_intel.models import validate_quotes

    result = pipeline.run_daily(wired, today="2026-09-14", only=["newswire"])
    madrigal = next(i for i in result.daily if i.title.startswith("Madrigal"))
    assert madrigal.evidence.quotes, "a P0 must carry source text behind its signal"
    source = "\n".join([madrigal.title, madrigal.meta.get("body", ""),
                        str(madrigal.meta.get("summary", ""))])
    assert validate_quotes(madrigal, source) == []
    body = result.report_path.read_text(encoding="utf-8")
    assert "met the primary endpoint" in body
