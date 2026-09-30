import json

import pytest

from liver_intel.discover import discover, scrape_page
from liver_intel.feeds import Feed, Registry
from liver_intel.grade import SIGNALS
from liver_intel.http import Blocked
from liver_intel.llm import (ClaudeJudge, EvaluativeLanguage, JudgeResult, NullJudge,
                             _verify, assert_not_evaluative, build_judge)
from liver_intel.models import Item
from liver_intel.verify import verify_feed, verify_registry


def feed(url="https://feed.example.com/rss", kind="rss", **kwargs):
    return Feed(id="f1", url=url, kind=kind, source="newswire", **kwargs)


# --- verification ---------------------------------------------------------
def test_feed_with_items_and_dates_is_verified(fake_fetcher):
    fake_fetcher.add(feed().url,
                     "<rss><channel><item><title>a</title>"
                     "<pubDate>Mon, 14 Sep 2026 08:00:00 GMT</pubDate></item></channel></rss>")
    result = verify_feed(feed(), fake_fetcher)
    assert result.ok and result.feed.status == "verified"
    assert result.feed.has_date is True and result.feed.item_count == 1


def test_feed_without_a_date_field_is_dead(fake_fetcher):
    fake_fetcher.add(feed().url, "<rss><channel><item><title>a</title></item></channel></rss>")
    result = verify_feed(feed(), fake_fetcher)
    assert not result.ok
    assert result.feed.status == "dead"
    assert "date field" in result.feed.note


def test_empty_feed_is_dead(fake_fetcher):
    fake_fetcher.add(feed().url, "<rss><channel></channel></rss>")
    assert verify_feed(feed(), fake_fetcher).feed.status == "dead"


def test_404_is_dead(fake_fetcher):
    fake_fetcher.add(feed().url, "not found", status=404)
    assert verify_feed(feed(), fake_fetcher).feed.status == "dead"


def test_403_is_blocked_and_not_retried(fake_fetcher):
    fake_fetcher.routes[feed().url] = Blocked("403")
    result = verify_feed(feed(), fake_fetcher)
    assert result.feed.status == "blocked"
    assert "manually" in result.feed.note


def test_json_api_verification(fake_fetcher):
    url = "https://api.example.com/x.json"
    fake_fetcher.add(url, json.dumps({"results": [{"title": "a", "date": "2026-09-14"}]}))
    assert verify_feed(feed(url=url, kind="json"), fake_fetcher).feed.status == "verified"


def test_registry_skips_discovery_roots(fake_fetcher):
    registry = Registry(entries=[Feed(id="root", url="https://example.com/",
                                      status="discover_root", source="newswire")])
    assert verify_registry(registry, fake_fetcher) == []


def test_collectors_see_nothing_until_verification(fake_fetcher):
    registry = Registry(entries=[feed(status="unverified")])
    assert registry.usable(source="newswire") == []
    fake_fetcher.add(feed().url,
                     "<rss><channel><item><title>a</title>"
                     "<pubDate>Mon, 14 Sep 2026 08:00:00 GMT</pubDate></item></channel></rss>")
    verify_registry(registry, fake_fetcher)
    assert len(registry.usable(source="newswire")) == 1


# --- discovery ------------------------------------------------------------
def test_autodiscovery_finds_head_links_and_feed_anchors():
    html = ('<html><head><link rel="alternate" type="application/rss+xml" '
            'title="News" href="/rss/news.xml"></head><body>'
            '<a href="/investors/feed">Investors</a><a href="/about">About</a></body></html>')
    found = scrape_page(html, "https://example.com/newsroom")
    assert [u for u, _, _ in found] == ["https://example.com/rss/news.xml",
                                        "https://example.com/investors/feed"]


def test_discovered_candidates_start_unverified(fake_fetcher):
    root = Feed(id="root", url="https://example.com/", status="discover_root",
                source="newswire", task="T1")
    fake_fetcher.add(root.url, '<link rel="alternate" type="application/rss+xml" href="/f.xml">')
    candidates = discover(root, fake_fetcher)
    assert all(c.status == "unverified" for c in candidates)
    assert any(c.kind == "sitemap" for c in candidates)


# --- significance step ----------------------------------------------------
SOURCE = ("Madrigal today announced that the Phase 3 MAESTRO-NASH trial met its "
          "primary endpoint. The company expects to file later this year.")


def test_signal_without_a_verifiable_quote_is_dropped():
    result = _verify([
        {"signal": "PH3_RESULT",
         "quotes": [{"text": "the Phase 3 MAESTRO-NASH trial met its primary endpoint"}]},
        {"signal": "REG_SUBMISSION",
         "quotes": [{"text": "the company has already submitted an NDA"}]},
    ], SOURCE)
    assert result.signals == ["PH3_RESULT"]
    assert any("no verifiable quote" in d for d in result.dropped)


def test_signal_outside_the_vocabulary_is_dropped():
    result = _verify([{"signal": "MOON_LANDING", "quotes": [{"text": SOURCE[:20]}]}], SOURCE)
    assert result.signals == []


def test_evaluative_translation_is_stripped_but_quote_kept():
    result = _verify([{"signal": "PH3_RESULT", "quotes": [
        {"text": "met its primary endpoint", "lang": "en", "translation": "这是重磅利好"}]}],
        SOURCE)
    assert result.signals == ["PH3_RESULT"]
    assert result.quotes[0].translation == ""


def test_evaluative_guard_catches_forecasts():
    with pytest.raises(EvaluativeLanguage):
        assert_not_evaluative("the readout is likely to support approval")


def test_null_judge_returns_nothing():
    item = Item(src="s", title="t", url="https://e.com/a", date="2026-09-14",
                meta={"src_kind": "company"})
    assert NullJudge().judge(item, SOURCE) == JudgeResult()


def test_build_judge_without_credentials_is_null(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert isinstance(build_judge(), NullJudge)


def test_claude_judge_verifies_quotes_against_the_document():
    class FakeResponse:
        stop_reason = "end_turn"
        content = [type("B", (), {"type": "text", "text": json.dumps({"findings": [
            {"signal": "PH3_RESULT", "quotes": [
                {"text": "met its primary endpoint", "locator": "para 1",
                 "lang": "en", "translation": ""}]},
            {"signal": "SAFETY_SERIOUS", "quotes": [
                {"text": "three patients died", "locator": "para 9",
                 "lang": "en", "translation": ""}]},
        ]})})()]
        parsed_output = None

    class FakeClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                assert kwargs["model"].startswith("claude-")
                schema = kwargs["output_config"]["format"]["schema"]
                enum = schema["properties"]["findings"]["items"]["properties"]["signal"]["enum"]
                assert set(enum) == set(SIGNALS)
                return FakeResponse()

    item = Item(src="s", title="t", url="https://e.com/a", date="2026-09-14",
                meta={"src_kind": "company"})
    result = ClaudeJudge(client=FakeClient()).judge(item, SOURCE)
    assert result.signals == ["PH3_RESULT"]        # the invented death is dropped


def test_claude_judge_survives_an_api_failure():
    class Boom:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("rate limited")

    item = Item(src="s", title="t", url="https://e.com/a", date="2026-09-14",
                meta={"src_kind": "company"})
    result = ClaudeJudge(client=Boom()).judge(item, SOURCE)
    assert result.signals == [] and "llm call failed" in result.dropped[0]


def test_transport_failure_is_unreachable_not_dead(fake_fetcher):
    """A proxy/DNS failure must not be recorded as a bad endpoint."""
    fake_fetcher.routes[feed().url] = ConnectionError("Tunnel connection failed")
    result = verify_feed(feed(), fake_fetcher)
    assert result.feed.status == "unreachable"
    assert "could not be reached" in result.feed.note


def test_unreachable_entries_are_reprobed_next_run(fake_fetcher):
    registry = Registry(entries=[feed(status="unreachable")])
    assert registry.usable(source="newswire") == []
    fake_fetcher.add(feed().url,
                     "<rss><channel><item><title>a</title>"
                     "<pubDate>Mon, 14 Sep 2026 08:00:00 GMT</pubDate></item></channel></rss>")
    verify_registry(registry, fake_fetcher)
    assert registry.entries[0].status == "verified"


def test_jsonp_endpoint_verifies(fake_fetcher):
    """HKEX's stock lookup answers with callback({...}); treating that as
    malformed JSON marked a working endpoint dead."""
    url = "https://www1.hkexnews.hk/search/prefix.do?"
    fake_fetcher.add(url, 'callback({"more":"1","stockInfo":[{"stockId":1,"code":"02137",'
                          '"name":"BRII-B","date":"2026-09-16"}]});')
    assert verify_feed(feed(url=url, kind="api"), fake_fetcher).feed.status == "verified"


def test_html_search_page_verifies_on_content(fake_fetcher):
    url = "https://www1.hkexnews.hk/search/titlesearch.xhtml"
    fake_fetcher.add(url, "<html>" + "x" * 900 + "</html>")
    assert verify_feed(feed(url=url, kind="html"), fake_fetcher).feed.status == "verified"


def test_empty_html_page_is_dead(fake_fetcher):
    url = "https://www1.hkexnews.hk/search/titlesearch.xhtml"
    fake_fetcher.add(url, "<html></html>")
    assert verify_feed(feed(url=url, kind="html"), fake_fetcher).feed.status == "dead"
