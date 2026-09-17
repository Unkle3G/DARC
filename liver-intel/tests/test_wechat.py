import re

from liver_intel.images import ImageCandidate, extract_images
from liver_intel.models import Item, Quote
from liver_intel.report_wechat import wechat_html


def make(priority="P0", lines=("L3",), title="Phase 3 topline",
         study=("PHASE3", "ENDPOINT_MET"), **meta):
    item = Item(src="newswire", title=title, url="https://www.example.com/a",
                date="2026-09-14", meta={"src_kind": "company", **meta},
                lines=list(lines), study=list(study), P=priority, score=9.2)
    item.evidence.signals = ["PH3_RESULT"]
    item.evidence.quotes = [Quote(text="met the primary endpoint", locator="para 1")]
    return item


# --- image extraction -----------------------------------------------------
HTML = """<html><head><meta property="og:image" content="/media/hero.jpg"></head><body>
<img src="/img/company-logo.png" width="400" height="300">
<img src="/img/kaplan-meier.png" width="800" height="600" alt="KM curve">
<img src="/img/pixel.gif" width="1" height="1">
<img src="data:image/png;base64,AAA">
<img src="/img/chart.svg" width="600" height="400"></body></html>"""


def test_only_content_images_survive():
    found = extract_images(HTML, "https://www.example.com/news/a")
    assert [c.url for c in found] == ["https://www.example.com/media/hero.jpg",
                                      "https://www.example.com/img/kaplan-meier.png"]


def test_social_image_ranks_first():
    assert extract_images(HTML, "https://www.example.com/news/a")[0].role == "social"


def test_tiny_and_furniture_images_are_dropped():
    urls = [c.url for c in extract_images(HTML, "https://www.example.com/news/a")]
    assert not any("logo" in u or "pixel" in u or u.endswith(".svg") for u in urls)


def body_of(article: str) -> str:
    """Everything above the sourcing note -- the part a reader scrolls."""
    return article.split("信源说明")[0]


# --- article rendering ----------------------------------------------------
def test_article_has_no_external_stylesheet(domain_map):
    article = wechat_html([make()], "2026-09-14", domain_map)
    assert "<style" not in article and "<link" not in article
    assert 'style="' in article           # everything is inline instead


def test_internal_grades_never_reach_the_reader(domain_map):
    """P0/P1/P2, line ids, signal names and adapter ids are triage, not copy."""
    item = make()
    item.meta["wire"] = "GlobeNewswire"
    article = wechat_html([item], "2026-09-14", domain_map)
    for internal in ("P0", "P1", "P2", "PH3_RESULT", "L3 ", "newswire", "score"):
        assert internal not in article, internal


def test_sections_use_reading_names(domain_map):
    items = [make("P0", title="a"), make("P1", title="b"), make("P2", title="c")]
    article = wechat_html(items, "2026-09-14", domain_map)
    assert "今日头条" in article and "前沿速览" in article and "最新动态" in article


def test_brand_is_hepadaily(domain_map):
    article = wechat_html([make()], "2026-09-14", domain_map)
    assert "HepaDaily" in article
    assert "肝病情报日报" not in article


def test_keywords_replace_engine_labels(domain_map):
    item = make()
    item.meta["companies"] = ["Madrigal Pharmaceuticals"]
    article = wechat_html([item], "2026-09-14", domain_map)
    assert "MASH" in article                      # from L3
    assert "III期临床" in article                  # from PHASE3
    assert "Madrigal Pharmaceuticals" in article
    assert "线路" not in article and "命中信号" not in article


def test_sourcing_note_sits_at_the_end(domain_map):
    item = make()
    article = wechat_html([item], "2026-09-14", domain_map)
    assert article.index(item.title) < article.index("不采用媒体转述")
    assert article.index(item.title) < article.index("信源说明")


def test_provenance_and_link_follow_each_entry(domain_map):
    """Provenance is not collected into a list at the end: each entry carries
    its own publisher, date and clickable original, before the next entry."""
    items = [make("P0", title="first entry"), make("P1", title="second entry")]
    for index, item in enumerate(items):
        item.url = f"https://www.example.com/{index}"
        item.meta["wire"] = "GlobeNewswire"
    article = wechat_html(items, "2026-09-14", domain_map)
    first_link = article.index(f'<a href="{items[0].url}"')
    assert article.index("first entry") < first_link < article.index("second entry")
    assert article.count("出处：GlobeNewswire · 2026-09-14") == 2
    assert "查看原文" in article
    assert "信源与原文" not in article


def test_foreign_title_keeps_the_original_first_and_its_rendering_after(domain_map):
    """Original first, rendering after -- the rule for titles as for quotes."""
    item = make(title="Phase 3 topline results in MASH")
    item.meta["title_zh"] = "MASH III期顶线结果"
    article = wechat_html([item], "2026-09-14", domain_map)
    assert "Phase 3 topline results in MASH</h2>" in article
    assert "MASH III期顶线结果" in article
    assert article.index("Phase 3 topline results in MASH</h2>") < article.index("MASH III期顶线结果")
    assert "编者译，仅供参考" in body_of(article)


def test_chinese_title_gets_nothing_added(domain_map):
    item = make(title="某公司III期临床达到主要终点")
    item.meta["title_zh"] = "should not appear"
    article = wechat_html([item], "2026-09-14", domain_map)
    assert "某公司III期临床达到主要终点</h2>" in article
    assert "should not appear" not in article


def test_registry_record_opens_with_its_sponsor(domain_map):
    item = make()
    item.src = "ctgov"
    item.meta["sponsor"] = "Roswell Park Cancer Institute"
    item.evidence.quotes = [Quote(text="awaiting agreement with Sponsor",
                                  locator="ClinicalTrials.gov · whyStopped")]
    article = wechat_html([item], "2026-09-14", domain_map)
    record = article[article.index("leadSponsor"):article.index("whyStopped")]
    assert "Roswell Park Cancer Institute" in record
    assert "出处：Roswell Park Cancer Institute · ClinicalTrials.gov" in article
    assert "Roswell Park Cancer Institute</span>" in article      # keyword chip too


def test_registry_field_rendering_sits_beside_the_value(domain_map):
    item = make()
    item.evidence.quotes = [
        Quote(text="Business Reasons", locator="ClinicalTrials.gov · whyStopped",
              translation="商业原因")]
    article = wechat_html([item], "2026-09-14", domain_map)
    assert "Business Reasons" in article and "｜商业原因" in article


# --- quotes and translation ----------------------------------------------
def test_chinese_quote_is_never_translated(domain_map):
    item = make()
    item.evidence.quotes = [Quote(text="该方案III期临床达到主要终点", lang="zh",
                                  translation="the trial met its primary endpoint")]
    article = wechat_html([item], "2026-09-14", domain_map)
    assert "该方案III期临床达到主要终点" in article
    assert "the trial met its primary endpoint" not in article
    assert "编者译" not in body_of(article)


def test_foreign_quote_keeps_original_and_labels_the_translation(domain_map):
    item = make()
    item.evidence.quotes = [Quote(text="met the primary endpoint", lang="en",
                                  translation="达到主要终点")]
    article = wechat_html([item], "2026-09-14", domain_map)
    assert "met the primary endpoint" in article
    assert "达到主要终点" in article
    assert "编者译，仅供参考" in article


def test_foreign_quote_without_a_translation_stands_alone(domain_map):
    """Rules-only runs have no model, so no translation is invented."""
    item = make()
    item.evidence.quotes = [Quote(text="met the primary endpoint", lang="en")]
    article = wechat_html([item], "2026-09-14", domain_map)
    assert "met the primary endpoint" in article
    assert "编者译" not in body_of(article)


def test_titles_are_escaped(domain_map):
    item = make(title='<script>alert("x")</script> Phase 3')
    article = wechat_html([item], "2026-09-14", domain_map)
    assert "<script>" not in article
    assert "&lt;script&gt;" in article


# --- figures --------------------------------------------------------------
def test_figure_is_rendered_with_a_caption(domain_map):
    item = make()
    item.meta["images"] = [ImageCandidate(
        url="https://www.example.com/media/hero.jpg", alt="KM curve",
        source_url="https://www.example.com/a").to_json()]
    article = wechat_html([item], "2026-09-14", domain_map)
    assert "https://www.example.com/media/hero.jpg" in article
    assert "KM curve" in article
    assert "版权归原发布方所有" in article


def test_item_without_a_figure_renders_cleanly(domain_map):
    article = wechat_html([make()], "2026-09-14", domain_map)
    assert "<figure" not in article


# --- shell ----------------------------------------------------------------
def test_watermark_is_prominent(domain_map):
    article = wechat_html([make()], "2026-09-14", domain_map, watermark="演示数据")
    assert "演示数据" in article
    assert article.index("演示数据") < article.index("HepaDaily")


def test_empty_day_still_renders(domain_map):
    article = wechat_html([], "2026-09-15", domain_map)
    assert "没有达到入选门槛" in article


def test_sections_follow_priority_order(domain_map):
    items = [make("P1", title="p1 item"), make("P0", title="p0 item")]
    article = wechat_html(items, "2026-09-14", domain_map)
    assert article.index("p0 item") < article.index("p1 item")


def test_entries_are_numbered_continuously(domain_map):
    items = [make("P0", title="a"), make("P0", title="b"), make("P1", title="c")]
    article = wechat_html(items, "2026-09-14", domain_map)
    numbers = re.findall(r'>(\d{2})</span>&nbsp;', article)
    assert numbers == ["01", "02", "03"]


def test_operator_notes_never_reach_the_reader(domain_map):
    article = wechat_html([make()], "2026-09-14", domain_map,
                          notes=["[newswire] no verified endpoint in the registry"])
    assert "no verified endpoint" not in article
    assert "运行提示" not in article


def test_reference_line_names_the_source(domain_map):
    """A registry or literature entry has no company, and was falling back to
    its date alone -- which tells a reader nothing about where it came from."""
    item = make(title="Phase 3 trial terminated")
    item.src = "ctgov"
    item.meta.pop("companies", None)
    article = wechat_html([item], "2026-09-14", domain_map)
    assert "ClinicalTrials.gov · 2026-09-14" in article


def test_registry_fields_render_as_a_record_not_pull_quotes(domain_map):
    item = make()
    item.evidence.quotes = [
        Quote(text="TERMINATED", locator="ClinicalTrials.gov · overallStatus"),
        Quote(text="Business Reasons", locator="ClinicalTrials.gov · whyStopped"),
    ]
    article = wechat_html([item], "2026-09-14", domain_map)
    assert "overallStatus：" in article and "TERMINATED" in article
    assert "<blockquote" not in article


def test_a_quote_that_repeats_the_headline_is_not_shown_twice(domain_map):
    item = make(title="Inventiva Announces Last Patient Visit in NATiV3")
    item.evidence.quotes = [
        Quote(text="Inventiva Announces Last Patient Visit in NATiV3", locator="tag:PHASE3"),
        Quote(text="Topline results of NATiV3 expected in Q4 2026", locator="tag:TOPLINE")]
    article = wechat_html([item], "2026-09-14", domain_map)
    assert article.count("Inventiva Announces Last Patient Visit in NATiV3") == 1
    assert "<blockquote" in article and "Topline results of NATiV3" in article


def test_conference_block_is_a_dated_timeline(domain_map, monkeypatch):
    from liver_intel import report_wechat
    from liver_intel.conference import Calendar, Conference
    calendar = Calendar(conferences=[Conference(
        id="X", name="Congress X", start="2026-11-05", end="2026-11-09", verified=True,
        location="Denver", abstract_close="2026-05-28", late_breaker_close="2026-09-25",
        late_breaker_release="2026-11-05")])
    block = report_wechat._conference_block("2026-09-17", calendar)
    assert "摘要投稿截止" in block and "2026-05-28　已过" in block
    assert "Late-breaker 投稿截止" in block and "2026-09-25" in block
    assert "Late-breaker embargo lift" in block
    assert "解禁" not in block and "摘要录用通知" not in block
    assert (block.index("摘要投稿截止") < block.index("Late-breaker 投稿截止")
            < block.index("Late-breaker embargo lift"))


def test_stcs_render_as_a_compact_list_under_the_annual_meetings(domain_map):
    from liver_intel import report_wechat
    from liver_intel.conference import Calendar, Conference
    calendar = Calendar(conferences=[
        Conference(id="A", name="Annual X", kind="annual", start="2026-11-05", end="2026-11-09",
                   verified=True, abstract_close="2026-05-28"),
        Conference(id="S", name="STC Almaty", kind="stc", start="2026-10-08", end="2026-10-09",
                   verified=True, location="Almaty, Kazakhstan")])
    block = report_wechat._conference_block("2026-09-17", calendar)
    assert "APASL 专题会（STC）" in block and "STC Almaty" in block and "Almaty, Kazakhstan" in block
    assert block.index("Annual X") < block.index("APASL 专题会（STC）") < block.index("STC Almaty")
    assert "2 条" in block
