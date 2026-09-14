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
    """Everything above the reference list -- the part a reader scrolls."""
    return article.split("信源与原文")[0]


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


def test_sourcing_note_and_links_sit_at_the_end(domain_map):
    item = make()
    article = wechat_html([item], "2026-09-14", domain_map)
    assert article.index(item.title) < article.index("不采用媒体转述")
    assert article.index(item.title) < article.index("信源与原文")


def test_original_link_is_clickable(domain_map):
    item = make()
    article = wechat_html([item], "2026-09-14", domain_map)
    assert f'<a href="{item.url}"' in article


def test_every_entry_appears_in_the_reference_list(domain_map):
    items = [make("P0", title="a"), make("P1", title="b")]
    for index, item in enumerate(items):
        item.url = f"https://www.example.com/{index}"
    article = wechat_html(items, "2026-09-14", domain_map)
    for item in items:
        assert f'<a href="{item.url}"' in article


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
