import re
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
    return Context(settings=Settings(allow_unverified=False, contact="ops@example.com"),
                   fetcher=fetcher,
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
    # Filers name the exhibit freely; it is identified by its declared Type.
    fake_fetcher.add(f"{folder}/0001-26-000001-index.htm", """
        <table>
        <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
        <tr><td>1</td><td>8-K</td><td>a.htm&nbsp;iXBRL</td><td>8-K</td><td>1</td></tr>
        <tr><td>2</td><td>EX-99.1</td><td>pressrelease-topline.htm</td><td>EX-99.1</td><td>2</td></tr>
        </table>""")
    fake_fetcher.add(f"{folder}/pressrelease-topline.htm",
                     "<p>EXHIBIT 99.1</p><p>Madrigal Announces Phase 3 Topline Results</p>")

    ctx = context(store, fake_fetcher, domain_map, [tickers, subs])
    items = EdgarSource().run(ctx)
    assert len(items) == 1                      # only the 7.01 filing
    assert items[0].meta["edgar_items"] == ["7.01"]
    assert "Phase 3 Topline" in items[0].title
    assert items[0].meta["exhibit_url"].endswith("pressrelease-topline.htm")
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
    # The registry does not say when it stopped, and the record must not
    # pretend a change happened today.
    assert item.meta["first_sighting"] is True
    assert item.meta["state_changes"].startswith("first sighting:")
    assert "none ->" not in item.meta["state_changes"]


def test_registry_sweep_is_bounded_and_newest_first(store, fake_fetcher, domain_map):
    """Unbounded, the API answers in relevance order and a daily run examined an
    arbitrary slice of the registry, surfacing old terminated trials as news."""
    from liver_intel.feeds import Feed
    feed = Feed(id="ctgov.v2", url="https://clinicaltrials.gov/api/v2/studies",
                source="ctgov", task="T3", status="verified", kind="api")
    fake_fetcher.add("https://clinicaltrials.gov/api/v2/studies", '{"studies": []}')
    ctx = context(store, fake_fetcher, domain_map, [feed])          # no since
    CtGovSource(terms=("MASH",)).collect(ctx)
    url = fake_fetcher.calls[-1]
    assert "filter.advanced=AREA%5BLastUpdatePostDate%5DRANGE%5B2026-09-07%2CMAX%5D" in url
    assert "sort=LastUpdatePostDate%3Adesc" in url


def test_edgar_refuses_to_run_without_a_contact_address(store, fake_fetcher, domain_map):
    ctx = Context(settings=Settings(contact=""), fetcher=fake_fetcher, store=store,
                  registry=Registry(entries=[]), domain_map=domain_map, today="2026-09-14")
    assert EdgarSource().collect(ctx) == []
    assert any("LIVER_INTEL_CONTACT" in note for note in ctx.notes)
    assert fake_fetcher.calls == []


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


# --- regressions found against live ClinicalTrials.gov data ----------------
def test_withdrawing_an_application_is_not_a_filing(domain_map):
    """A real registry entry read 'requests the permanent discontinuation and
    withdrawal of Investigational New Drug application IND 152626'. The bare-id
    pattern reported that as a regulatory filing -- the opposite of the fact."""
    from liver_intel.tagger import Tagger

    text = ("T-ACE Medical Co., Ltd. hereby submits this formal notification to the "
            "U.S. Food and Drug Administration (FDA) to request the permanent "
            "discontinuation and withdrawal of Investigational New Drug application "
            "IND 152626.")
    assert "SUBMISSION" not in Tagger(domain_map).tag_study(text)


def test_a_real_filing_still_fires(domain_map):
    from liver_intel.tagger import Tagger

    tagger = Tagger(domain_map)
    assert "SUBMISSION" in tagger.tag_study("The company submitted an NDA to the FDA.")
    assert "SUBMISSION" in tagger.tag_study("The NDA was accepted for priority review.")
    assert "SUBMISSION" in tagger.tag_study("该品种上市申请获得受理")


def test_a_bare_application_id_is_not_a_filing(domain_map):
    from liver_intel.tagger import Tagger

    assert "SUBMISSION" not in Tagger(domain_map).tag_study(
        "The study operated under IND 152626 since 2019.")


def test_results_already_on_file_are_not_a_readout(store, fake_fetcher, domain_map):
    """First sighting of a study whose results were posted years ago is not news."""
    ctx = context(store, fake_fetcher, domain_map, since="2026-08-17")
    item = CtGovSource()._diff_to_item(
        ctx, study(status="TERMINATED", why="Business Reasons", results="2021-03-01"))
    assert item is not None
    assert "TOPLINE" not in item.study


def test_results_posted_inside_the_window_are_a_readout(store, fake_fetcher, domain_map):
    ctx = context(store, fake_fetcher, domain_map, since="2026-08-17")
    item = CtGovSource()._diff_to_item(
        ctx, study(status="TERMINATED", why="Business Reasons", results="2026-09-01"))
    assert "TOPLINE" in item.study


def test_evidence_never_quotes_engine_scaffolding(store, fake_fetcher, domain_map):
    """meta.body carries lines we wrote ('Phase: PHASE3'); meta.quotable carries
    only what the registry published."""
    ctx = context(store, fake_fetcher, domain_map)
    item = CtGovSource()._diff_to_item(
        ctx, study(status="TERMINATED", why="Business Reasons"))
    assert "Phase: PHASE3" in item.meta["body"]
    assert "Phase: PHASE3" not in item.meta["quotable"]
    assert "Business Reasons" in item.meta["quotable"]


def test_registry_evidence_cites_fields_not_prose(store, fake_fetcher, domain_map):
    """A registry record states its facts in fields. Evidence quotes the published
    values with the field as the locator, rather than leaving a P0 unsupported."""
    ctx = context(store, fake_fetcher, domain_map)
    item = CtGovSource()._diff_to_item(
        ctx, study(status="TERMINATED", why="Business Reasons"))
    cited = {q.locator: q.text for q in item.evidence.quotes}
    assert cited["ClinicalTrials.gov · overallStatus"] == "TERMINATED"
    assert cited["ClinicalTrials.gov · whyStopped"] == "Business Reasons"
    assert all(q.url.endswith("NCT0001") for q in item.evidence.quotes)


def test_field_evidence_is_verifiable_against_the_quotable_text(store, fake_fetcher,
                                                                domain_map):
    from liver_intel.models import validate_quotes

    ctx = context(store, fake_fetcher, domain_map)
    item = CtGovSource()._diff_to_item(
        ctx, study(status="TERMINATED", why="Business Reasons"))
    quotable = "\n".join([item.title, item.meta["quotable"]])
    assert validate_quotes(item, quotable) == []


def test_exhibit_is_found_by_type_not_filename():
    """A real Madrigal 8-K named its press release `pressrelease-boardappointm.htm`;
    matching on an `ex99*` filename found nothing at all."""
    from liver_intel.sources.edgar import exhibit_name

    index = """<table>
      <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
      <tr><td>1</td><td>8-K</td><td>mdgl-20260811.htm&nbsp;&nbsp;iXBRL</td><td>8-K</td><td>31809</td></tr>
      <tr><td>2</td><td>EX-99.1</td><td>pressrelease-boardappointm.htm</td><td>EX-99.1</td><td>12207</td></tr>
      <tr><td>6</td><td></td><td>imagea.jpg</td><td>GRAPHIC</td><td>10012</td></tr>
    </table>"""
    assert exhibit_name(index) == "pressrelease-boardappointm.htm"


def test_ex99_1_wins_over_other_exhibits():
    from liver_intel.sources.edgar import exhibit_name

    index = """<table>
      <tr><td>2</td><td>EX-99.2</td><td>slides.htm</td><td>EX-99.2</td><td>1</td></tr>
      <tr><td>3</td><td>EX-99.1</td><td>release.htm</td><td>EX-99.1</td><td>2</td></tr>
    </table>"""
    assert exhibit_name(index) == "release.htm"


def test_filing_without_an_exhibit_yields_nothing():
    from liver_intel.sources.edgar import exhibit_name

    assert exhibit_name(
        "<table><tr><td>1</td><td>8-K</td><td>a.htm</td><td>8-K</td><td>1</td></tr></table>") == ""


def test_headline_skips_the_exhibit_wrapper_but_finds_the_real_one():
    """`lead` must offer several lines: taking only the first left one filing
    titled `8-K` because its opening line was the wrapper."""
    from liver_intel.sources.edgar import _first_headline

    body = ("EX-99.1\n2\na20260805-q2ex991earningsr.htm\nEX-99.1\n\nExhibit 99.1\n\n"
            "Altimmune Announces Positive Topline Results from RECLAIM Phase 2 Trial")
    assert _first_headline(body).startswith("Altimmune Announces Positive Topline")


def test_headline_is_empty_when_there_is_none():
    from liver_intel.sources.edgar import _first_headline

    assert _first_headline("EX-99.1\n2\nshort.htm") == ""


def test_a_pinned_cik_survives_delisting(store, fake_fetcher, domain_map):
    """company_tickers.json lists only currently-listed tickers, so an acquired
    filer disappears from it while its filings remain."""
    from liver_intel.domain_map import Company

    tickers = Feed(id="sec.company_tickers",
                   url="https://www.sec.gov/files/company_tickers.json",
                   source="edgar", status="verified")
    subs = Feed(id="sec.submissions", url="https://data.sec.gov/submissions/",
                source="edgar", status="verified")
    fake_fetcher.add(tickers.url, json.dumps({}))          # ticker is gone
    fake_fetcher.add("https://data.sec.gov/submissions/CIK0001744659.json", json.dumps({
        "filings": {"recent": {
            "form": ["8-K"], "items": ["7.01"], "filingDate": ["2026-09-14"],
            "accessionNumber": ["0001-26-000001"], "primaryDocument": ["a.htm"],
            "primaryDocDescription": ["8-K"], "reportDate": ["2026-09-14"]}}}))
    fake_fetcher.add("https://www.sec.gov/Archives/edgar/data/1744659/", "")

    ctx = context(store, fake_fetcher, domain_map, [tickers, subs])
    ctx.domain_map.companies = [Company(name="Akero Therapeutics", ticker="AKRO",
                                        cik=1744659, market="us", tier=1)]
    items = EdgarSource().run(ctx)
    assert len(items) == 1
    assert items[0].meta["cik"] == 1744659


def test_unresolved_companies_are_named_once(store, fake_fetcher, domain_map):
    from liver_intel.domain_map import Company

    tickers = Feed(id="sec.company_tickers",
                   url="https://www.sec.gov/files/company_tickers.json",
                   source="edgar", status="verified")
    subs = Feed(id="sec.submissions", url="https://data.sec.gov/submissions/",
                source="edgar", status="verified")
    fake_fetcher.add(tickers.url, json.dumps({}))
    ctx = context(store, fake_fetcher, domain_map, [tickers, subs])
    ctx.domain_map.companies = [Company(name="Gone Inc", ticker="GONE", market="us")]
    assert EdgarSource().run(ctx) == []
    assert any("no CIK for: Gone Inc" in note for note in ctx.notes)


def test_foreign_issuers_6k_is_collected(store, fake_fetcher, domain_map):
    """Novo Nordisk, GSK, AstraZeneca and Takeda filed no 8-K over a recent
    quarter -- only 6-K, which carries no Item codes."""
    from liver_intel.domain_map import Company

    tickers = Feed(id="sec.company_tickers",
                   url="https://www.sec.gov/files/company_tickers.json",
                   source="edgar", status="verified")
    subs = Feed(id="sec.submissions", url="https://data.sec.gov/submissions/",
                source="edgar", status="verified")
    fake_fetcher.add(tickers.url, json.dumps(
        {"0": {"cik_str": 353278, "ticker": "NVO", "title": "NOVO NORDISK A S"}}))
    fake_fetcher.add("https://data.sec.gov/submissions/CIK0000353278.json", json.dumps({
        "filings": {"recent": {
            "form": ["6-K"], "items": [""], "filingDate": ["2026-09-14"],
            "accessionNumber": ["0001-26-000009"], "primaryDocument": ["a.htm"],
            "primaryDocDescription": ["6-K"], "reportDate": ["2026-09-14"]}}}))
    fake_fetcher.add("https://www.sec.gov/Archives/edgar/data/353278/", "")

    ctx = context(store, fake_fetcher, domain_map, [tickers, subs])
    ctx.domain_map.companies = [Company(name="Novo Nordisk", ticker="NVO",
                                        market="eu", tier=1)]
    items = EdgarSource().run(ctx)
    assert len(items) == 1
    assert items[0].meta["form"] == "6-K"
    assert items[0].meta["form_is_itemless"] is True


def test_8k_still_needs_a_wanted_item_code(store, fake_fetcher, domain_map):
    from liver_intel.domain_map import Company

    tickers = Feed(id="sec.company_tickers",
                   url="https://www.sec.gov/files/company_tickers.json",
                   source="edgar", status="verified")
    subs = Feed(id="sec.submissions", url="https://data.sec.gov/submissions/",
                source="edgar", status="verified")
    fake_fetcher.add(tickers.url, json.dumps(
        {"0": {"cik_str": 1, "ticker": "X", "title": "X"}}))
    fake_fetcher.add("https://data.sec.gov/submissions/CIK0000000001.json", json.dumps({
        "filings": {"recent": {
            "form": ["8-K"], "items": ["5.02"], "filingDate": ["2026-09-14"],
            "accessionNumber": ["0001-26-000010"], "primaryDocument": ["a.htm"],
            "primaryDocDescription": ["8-K"], "reportDate": ["2026-09-14"]}}}))
    ctx = context(store, fake_fetcher, domain_map, [tickers, subs])
    ctx.domain_map.companies = [Company(name="X", ticker="X", market="us")]
    assert EdgarSource().run(ctx) == []


def test_hkex_rows_parse_without_the_mobile_labels():
    from liver_intel.sources.hkex import parse_results

    html = """<tr>
      <td class="text-right release-time"><span class="mobile-list-heading">Release Time: </span>16/09/2026 18:04</td>
      <td class="stock-short-code"><span class="mobile-list-heading">Stock Code: </span>02137</td>
      <td class="stock-short-name"><span class="mobile-list-heading">Stock Short Name: </span>BRII-B</td>
      <td><div class="headline">Announcements and Notices - [Other - Business Update]</div>
          <div class="doc-link"><a href="/listedco/listconews/sehk/2026/0916/x.pdf">Business Update</a></div>
      </td></tr>"""
    row = parse_results(html)[0]
    assert row["date"] == "2026-09-16"
    assert row["code"] == "02137"
    assert row["short_name"] == "BRII-B"
    assert row["url"].endswith("/2026/0916/x.pdf")


def test_hkex_title_comes_from_inside_the_pdf():
    """The search headline is only a category label; the announcement's own
    title is in the document."""
    from liver_intel.sources.hkex import _announcement_title

    body = ("Hong Kong Exchanges and Clearing Limited ... expressly disclaim any "
            "liability whatsoever for any loss howsoever arising from or in reliance "
            "upon the whole or any part of the contents of this announcement.\n"
            "Ascletis Pharma Inc.\n(Stock Code: 1672)\n"
            "VOLUNTARY ANNOUNCEMENT\n"
            "ASCLETIS ANNOUNCES INITIATION OF PHASE I STUDY IN U.S.\n\n"
            "- This trial marks the fourth Phase I peptide study this year.")
    assert _announcement_title(body) == "ASCLETIS ANNOUNCES INITIATION OF PHASE I STUDY IN U.S."


# --- openFDA sponsor lookup ------------------------------------------------
def test_sponsor_token_matches_how_drugsfda_files_a_company():
    """drugsfda stores sponsors short and upper-cased -- MADRIGAL, GILEAD,
    MIRUM. Querying the full company name matched nothing for anyone."""
    from liver_intel.sources.regulator import sponsor_token

    assert sponsor_token("Madrigal Pharmaceuticals") == "MADRIGAL"
    assert sponsor_token("Gilead Sciences") == "GILEAD"
    assert sponsor_token("AbbVie") == "ABBVIE"


def test_sponsor_token_prefers_a_four_letter_word():
    """'Eli Lilly' is filed under LILLY; ELI would be the wrong company."""
    from liver_intel.sources.regulator import sponsor_token

    assert sponsor_token("Eli Lilly") == "LILLY"


def test_sponsor_token_falls_back_for_short_names():
    from liver_intel.sources.regulator import sponsor_token

    assert sponsor_token("GSK") == "GSK"


def test_sponsor_token_keeps_a_leading_number():
    from liver_intel.sources.regulator import sponsor_token

    assert sponsor_token("89bio") == "89BIO"


def test_sponsor_token_skips_words_identifying_no_one():
    from liver_intel.sources.regulator import sponsor_token

    assert sponsor_token("The New Company") == "COMPANY"


def test_registry_phases_are_structural_tags():
    from liver_intel.sources.ctgov import _phase_tags
    assert _phase_tags("PHASE1, PHASE2") == ["PHASE1", "PHASE2"]
    assert _phase_tags("EARLY_PHASE1") == ["PHASE1"]
    assert _phase_tags("NA") == [] and _phase_tags(None) == []


# --- literature -----------------------------------------------------------
def test_literature_item_carries_its_abstract(store, fake_fetcher, domain_map):
    """A bare title fired PUBLICATION alone and graded P3 forever."""
    from liver_intel.grade import apply as grade
    from liver_intel.sources.pubmed import PubmedSource
    from liver_intel.tagger import Tagger

    ctx = context(store, fake_fetcher, domain_map)
    item = PubmedSource()._to_item(
        ctx, "111",
        {"title": "Resmetirom in MASH: a randomised trial",
         "fulljournalname": "Journal of hepatology", "sortpubdate": "2026/09/14"},
        "RESULTS: The Phase 3 trial met the primary endpoint of MASH resolution "
        "on liver biopsy.")
    assert item is not None
    assert item.meta["has_abstract"] is True
    assert "met the primary endpoint" in item.meta["body"]
    # the abstract is published by the journal, so it may be quoted as evidence
    assert "met the primary endpoint" in item.meta["quotable"]

    Tagger(domain_map).apply(item)
    grade(item, dm=domain_map, today="2026-09-14")
    assert item.lines == ["L3"]
    assert {"PHASE3", "ENDPOINT_MET", "BIOPSY_ENDPOINT"} <= set(item.study)
    assert item.P != "P3", item.why


def test_an_article_without_an_abstract_still_collects(store, fake_fetcher, domain_map):
    from liver_intel.sources.pubmed import PubmedSource
    ctx = context(store, fake_fetcher, domain_map)
    item = PubmedSource()._to_item(
        ctx, "222", {"title": "Editorial on MASH", "fulljournalname": "Hepatology"}, "")
    assert item is not None and item.meta["has_abstract"] is False
    assert item.meta["body"] == "Editorial on MASH"


def test_a_correction_notice_is_not_collected_as_a_paper(store, fake_fetcher, domain_map):
    """"Corrigendum to: COMMD10 inhibits HIF1a/CP loop..." shipped as an item.

    A correction carries a title and an abstract like any article, so nothing
    downstream can tell it apart; PubMed's own publication type can.
    """
    from liver_intel.sources.pubmed import PubmedSource

    source = PubmedSource()
    ctx = context(store, fake_fetcher, domain_map)
    record = {"title": "Corrigendum to: COMMD10 inhibits the HIF1a/CP loop.",
              "fulljournalname": "Hepatology", "sortpubdate": "2026/09/16 00:00",
              "pubtype": ["Published Erratum", "Journal Article"], "authors": []}
    assert source._to_item(ctx, "1", record, "A correction to the original.") is None
    # esummary also hands back a non-standard spelling: "RETRACTION: Long
    # Noncoding RNA NR2F1-AS1..." arrived as "Retraction Notice" and sailed
    # past the indexed names into the pool.
    record["pubtype"] = ["Retraction Notice", "Journal Article"]
    assert source._to_item(ctx, "2", record, "This article has been retracted.") is None
    record["pubtype"] = ["Journal Article"]
    assert source._to_item(ctx, "3", record, "A correction to the original.") is not None


def test_the_literature_window_is_paged_not_truncated(store, fake_fetcher, domain_map):
    """241 hits over three days against a retmax of 50 dropped four papers in five.

    Worse, esearch sorts by index date, so *which* fifth survived depended on
    the minute the run fired: a paper collected in one run was gone from the
    next an hour later.
    """
    from liver_intel.sources import pubmed as pubmed_mod

    seen: list[str] = []

    class Paging:
        ok, status = True, 200
        text = "<PubmedArticleSet></PubmedArticleSet>"

        def __init__(self, url):
            self.url = url

        def json(self):
            if "esearch" in self.url:
                start = int(re.search(r"retstart=(\d+)", self.url).group(1))
                size = int(re.search(r"retmax=(\d+)", self.url).group(1))
                ids = [str(1000 + i) for i in range(start, min(start + size, 241))]
                return {"esearchresult": {"count": "241", "idlist": ids}}
            return {"result": {"uids": []}}

    class Fetcher:
        def get(self, url, allow_304=True):
            seen.append(url)
            return Paging(url)

    source = pubmed_mod.PubmedSource()
    ctx = context(store, Fetcher(), domain_map,
                  entries=[Feed(id="pubmed_esummary", source="pubmed",
                                     url="https://eutils.ncbi.nlm.nih.gov/esummary.fcgi",
                                     status="verified")])
    ids, total = source._search(ctx)
    assert total == 241
    assert len(ids) == 241            # the whole window, not the first 50
    assert len(set(ids)) == 241       # and no page repeated
