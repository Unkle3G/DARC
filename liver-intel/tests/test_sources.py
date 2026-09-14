import json

from liver_intel.config import Settings
from liver_intel.feeds import Feed, Registry
from liver_intel.sources.base import Context
from liver_intel.sources.ctgov import CtGovSource, _flatten
from liver_intel.sources.edgar import EdgarSource
from liver_intel.sources.newswire import NewswireSource, parse_feed
from liver_intel.sources.regulator import CnRegulatorSource
from liver_intel.sources.xalert import XAlertSource


def context(store, fetcher, domain_map, entries=(), **kwargs):
    return Context(settings=Settings(allow_unverified=False), fetcher=fetcher,
                   store=store, registry=Registry(entries=list(entries)),
                   domain_map=domain_map, today="2026-09-14", **kwargs)


# --- T1 -------------------------------------------------------------------
RSS = """<rss><channel>
<item><title><![CDATA[Madrigal reports Phase 3 MASH topline]]></title>
<link>https://www.globenewswire.com/news-release/2026/09/14/a.html</link>
<pubDate>Mon, 14 Sep 2026 08:00:00 GMT</pubDate>
<description>Topline results</description></item>
<item><title>Old item</title>
<link>https://www.globenewswire.com/news-release/2020/01/01/b.html</link>
<pubDate>Wed, 01 Jan 2020 08:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_rss_parsing_extracts_date_and_link():
    entries = parse_feed(RSS, "https://feed")
    assert entries[0]["date"] == "2026-09-14"
    assert entries[0]["title"] == "Madrigal reports Phase 3 MASH topline"


def test_newswire_only_uses_verified_feeds(store, fake_fetcher, domain_map):
    unverified = Feed(id="f1", url="https://feed.example.com/rss", source="newswire",
                      status="unverified")
    ctx = context(store, fake_fetcher, domain_map, [unverified])
    assert NewswireSource().run(ctx) == []
    assert any("no verified endpoint" in note for note in ctx.notes)
    assert fake_fetcher.calls == []


def test_newswire_collects_from_a_verified_feed(store, fake_fetcher, domain_map):
    feed = Feed(id="f1", url="https://feed.example.com/rss", source="newswire",
                status="verified")
    fake_fetcher.add("https://feed.example.com/rss", RSS)
    fake_fetcher.add("https://www.globenewswire.com/news-release/2026/09/14/a.html",
                     "<p>Madrigal today announced topline results.</p>")
    fake_fetcher.add("https://www.globenewswire.com/news-release/2020/01/01/b.html", "<p>old</p>")
    ctx = context(store, fake_fetcher, domain_map, [feed], since="2026-09-01")
    items = NewswireSource().run(ctx)
    assert [i.date for i in items] == ["2026-09-14"]
    assert items[0].meta["wire"] == "GlobeNewswire"
    assert "Madrigal today announced" in items[0].meta["body"]


def test_newswire_skips_items_already_published(store, fake_fetcher, domain_map):
    feed = Feed(id="f1", url="https://feed.example.com/rss", source="newswire",
                status="verified")
    fake_fetcher.add("https://feed.example.com/rss", RSS)
    fake_fetcher.add("https://www.globenewswire.com/news-release/", "<p>body</p>")
    ctx = context(store, fake_fetcher, domain_map, [feed], since="2026-09-01")
    first = NewswireSource().run(ctx)
    store.mark_seen(first[0], published_on="2026-09-14")
    assert NewswireSource().run(ctx) == []


# --- T2 -------------------------------------------------------------------
def test_edgar_filters_to_the_wanted_8k_items(store, fake_fetcher, domain_map):
    tickers = Feed(id="sec.company_tickers",
                   url="https://www.sec.gov/files/company_tickers.json",
                   source="edgar", status="verified")
    subs = Feed(id="sec.submissions", url="https://data.sec.gov/submissions/",
                source="edgar", status="verified")
    fake_fetcher.add(tickers.url, json.dumps({"0": {"cik_str": 1157601, "ticker": "MDGL",
                                                    "title": "Madrigal"}}))
    fake_fetcher.add("https://data.sec.gov/submissions/CIK0001157601.json", json.dumps({
        "filings": {"recent": {
            "form": ["8-K", "8-K", "4"],
            "items": ["7.01,9.01", "5.02", "7.01"],
            "filingDate": ["2026-09-14", "2026-09-10", "2026-09-09"],
            "accessionNumber": ["0001-26-000001", "0001-26-000002", "0001-26-000003"],
            "primaryDocument": ["a.htm", "b.htm", "c.htm"],
            "primaryDocDescription": ["8-K", "8-K", "4"],
            "reportDate": ["2026-09-14", "2026-09-10", "2026-09-09"],
        }}}))
    folder = "https://www.sec.gov/Archives/edgar/data/1157601/000126000001"
    fake_fetcher.add(f"{folder}/index.json", json.dumps({
        "directory": {"item": [{"name": "a.htm"}, {"name": "ex991.htm"}]}}))
    fake_fetcher.add(f"{folder}/ex991.htm",
                     "<p>EXHIBIT 99.1</p><p>Madrigal Announces Phase 3 Topline Results</p>")

    ctx = context(store, fake_fetcher, domain_map, [tickers, subs])
    items = EdgarSource().run(ctx)
    assert len(items) == 1                      # only the 7.01 filing
    assert items[0].meta["edgar_items"] == ["7.01"]
    assert "Phase 3 Topline" in items[0].title
    assert items[0].meta["exhibit_url"].endswith("ex991.htm")
    assert items[0].meta["src_kind"] == "filing"


def test_edgar_skipped_when_the_endpoint_is_unverified(store, fake_fetcher, domain_map):
    subs = Feed(id="sec.submissions", url="https://data.sec.gov/submissions/",
                source="edgar", status="unverified")
    ctx = context(store, fake_fetcher, domain_map, [subs])
    assert EdgarSource().run(ctx) == []
    assert fake_fetcher.calls == []


# --- T3 -------------------------------------------------------------------
def study(status="RECRUITING", why=None, last_update="2026-09-01", results=None):
    return _flatten({"protocolSection": {
        "identificationModule": {"nctId": "NCT0001", "briefTitle": "Phase 3 cirrhosis study"},
        "statusModule": {"overallStatus": status, "whyStopped": why,
                         "lastUpdatePostDateStruct": {"date": last_update},
                         "resultsFirstPostDateStruct": {"date": results} if results else {}},
        "designModule": {"phases": ["PHASE3"]},
        "conditionsModule": {"conditions": ["Liver Cirrhosis"]},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Acme"}},
    }})


def test_first_sighting_of_a_routine_trial_is_silent(store, fake_fetcher, domain_map):
    ctx = context(store, fake_fetcher, domain_map)
    assert CtGovSource()._diff_to_item(ctx, study()) is None


def test_last_update_only_change_produces_nothing(store, fake_fetcher, domain_map):
    ctx = context(store, fake_fetcher, domain_map)
    source = CtGovSource()
    source._diff_to_item(ctx, study())
    assert source._diff_to_item(ctx, study(last_update="2026-09-13")) is None


def test_status_change_produces_an_item(store, fake_fetcher, domain_map):
    ctx = context(store, fake_fetcher, domain_map)
    source = CtGovSource()
    source._diff_to_item(ctx, study())
    item = source._diff_to_item(
        ctx, study(status="TERMINATED", why="sponsor decision after futility analysis"))
    assert item is not None
    assert "RECRUITING -> TERMINATED" in item.meta["state_changes"]
    assert set(item.study) >= {"PHASE3", "TERMINATED"}
    assert item.date == "2026-09-14"           # archived by collection date


def test_results_posting_produces_an_item(store, fake_fetcher, domain_map):
    ctx = context(store, fake_fetcher, domain_map)
    source = CtGovSource()
    source._diff_to_item(ctx, study())
    item = source._diff_to_item(ctx, study(results="2026-09-14"))
    assert item is not None and "TOPLINE" in item.study


def test_already_terminated_trial_is_reported_on_first_sighting(store, fake_fetcher, domain_map):
    ctx = context(store, fake_fetcher, domain_map)
    item = CtGovSource()._diff_to_item(
        ctx, study(status="TERMINATED", why="enrolment futility"))
    assert item is not None


# --- T4 -------------------------------------------------------------------
def test_cn_regulator_reports_only_real_changes(store, fake_fetcher, domain_map):
    feed = Feed(id="cde.list", url="https://www.cde.org.cn/list", source="cn_regulator",
                status="verified", note="CDE 受理品种列表变更")
    page_a = "<ul><li>药品A受理</li></ul><span>2026-09-14 08:00:01</span>"
    page_b = "<ul><li>药品A受理</li></ul><span>2026-09-14 09:30:12</span>"
    page_c = "<ul><li>药品A受理</li><li>药品B受理</li></ul>"
    ctx = context(store, fake_fetcher, domain_map, [feed])
    source = CnRegulatorSource()

    fake_fetcher.add(feed.url, page_a)
    assert source.run(ctx) == []                     # first sighting: baseline only
    fake_fetcher.add(feed.url, page_b)
    assert source.run(ctx) == []                     # cosmetic re-render
    fake_fetcher.add(feed.url, page_c)
    items = source.run(ctx)
    assert len(items) == 1 and items[0].meta["needs_human_read"] is True


def test_blocked_chinese_site_downgrades_to_a_manual_prompt(store, fake_fetcher, domain_map):
    from liver_intel.http import Blocked

    feed = Feed(id="nmpa.list", url="https://www.nmpa.gov.cn/list", source="cn_regulator",
                status="verified")
    fake_fetcher.routes[feed.url] = Blocked("403")
    ctx = context(store, fake_fetcher, domain_map, [feed])
    assert CnRegulatorSource().run(ctx) == []
    assert any("by hand" in note for note in ctx.notes)


# --- T8 -------------------------------------------------------------------
def test_x_alert_layer_is_off_by_default(store, fake_fetcher, domain_map):
    ctx = context(store, fake_fetcher, domain_map)
    assert XAlertSource().run(ctx) == []
    assert any("disabled" in note for note in ctx.notes)


def test_x_alert_extracts_only_primary_links():
    links = XAlertSource.primary_links(
        "data out https://t.co/x https://www.globenewswire.com/a https://endpts.com/b")
    assert links == ["https://www.globenewswire.com/a"]
