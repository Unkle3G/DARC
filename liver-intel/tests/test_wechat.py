import re

from liver_intel.images import ImageCandidate, extract_images
from liver_intel.models import Item, Quote
from liver_intel.report_wechat import wechat_html


def make(priority="P0", lines=("L3",), title="Phase 3 topline", **meta):
    item = Item(src="newswire", title=title, url="https://www.example.com/a",
                date="2026-09-14", meta={"src_kind": "company", **meta},
                lines=list(lines), P=priority, score=9.2)
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


# --- article rendering ----------------------------------------------------
def test_article_has_no_external_stylesheet(domain_map):
    article = wechat_html([make()], "2026-09-14", domain_map)
    assert "<style" not in article and "<link" not in article
    assert 'style="' in article           # everything is inline instead


def test_article_prints_url_and_quote(domain_map):
    item = make()
    article = wechat_html([item], "2026-09-14", domain_map)
    assert item.url in article
    assert "met the primary endpoint" in article
    assert "PH3_RESULT" in article


def test_article_states_the_sourcing_rules(domain_map):
    article = wechat_html([make()], "2026-09-14", domain_map)
    assert "不采用媒体转述" in article
    assert "不作推断" in article


def test_titles_are_escaped(domain_map):
    item = make(title='<script>alert("x")</script> Phase 3')
    article = wechat_html([item], "2026-09-14", domain_map)
    assert "<script>" not in article
    assert "&lt;script&gt;" in article


def test_figure_is_rendered_with_its_source(domain_map):
    item = make()
    item.meta["images"] = [ImageCandidate(
        url="https://www.example.com/media/hero.jpg", alt="KM curve",
        source_url="https://www.example.com/a").to_json()]
    article = wechat_html([item], "2026-09-14", domain_map)
    assert "https://www.example.com/media/hero.jpg" in article
    assert "KM curve" in article
    assert "来源：https://www.example.com/a" in article
    assert "公众号不支持外链图片" in article


def test_local_figure_prompts_upload(domain_map):
    item = make()
    item.meta["images"] = [ImageCandidate(
        url="https://www.example.com/media/hero.jpg",
        local_path="/tmp/out/images/ab/00.jpg",
        source_url="https://www.example.com/a").to_json()]
    article = wechat_html([item], "2026-09-14", domain_map)
    assert "/tmp/out/images/ab/00.jpg" in article
    assert "需在公众号编辑器中上传" in article


def test_item_without_a_figure_renders_cleanly(domain_map):
    article = wechat_html([make()], "2026-09-14", domain_map)
    assert "<figure" not in article


def test_watermark_is_prominent(domain_map):
    article = wechat_html([make()], "2026-09-14", domain_map, watermark="演示数据")
    assert "演示数据" in article
    assert article.index("演示数据") < article.index("肝病情报日报")


def test_empty_day_still_renders(domain_map):
    article = wechat_html([], "2026-09-15", domain_map)
    assert "无条目" in article


def test_sections_follow_priority_order(domain_map):
    items = [make("P1", title="p1 item"), make("P0", title="p0 item")]
    article = wechat_html(items, "2026-09-14", domain_map)
    assert article.index("p0 item") < article.index("p1 item")


def test_entries_are_numbered_continuously(domain_map):
    items = [make("P0", title="a"), make("P0", title="b"), make("P1", title="c")]
    article = wechat_html(items, "2026-09-14", domain_map)
    numbers = re.findall(r'>(\d{2})</span>&nbsp;', article)
    assert numbers == ["01", "02", "03"]
