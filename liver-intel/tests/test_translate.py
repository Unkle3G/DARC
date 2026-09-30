"""The rendering step: literal Chinese for what ships, never invented."""
from __future__ import annotations

import json
from types import SimpleNamespace

from liver_intel import translate
from liver_intel.models import Item, Quote


def make(title="Phase 3 trial met the primary endpoint", quotes=()):
    item = Item(src="newswire", title=title, url="https://www.example.com/a",
                date="2026-09-14", meta={"src_kind": "company"}, P="P0")
    item.evidence.quotes = list(quotes)
    return item


class FakeClient:
    """Answers with whatever renderings the test hands it, by index."""

    def __init__(self, renderings):
        self.renderings = renderings
        self.requests = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        payload = {"translations": [{"index": i, "zh": zh}
                                    for i, zh in enumerate(self.renderings)]}
        return SimpleNamespace(stop_reason="end_turn", parsed_output=None,
                               content=[SimpleNamespace(type="text",
                                                        text=json.dumps(payload))])


def test_null_translator_leaves_originals_alone():
    item = make(quotes=[Quote(text="met the primary endpoint")])
    assert translate.apply(item, translate.NullTranslator()) == 0
    assert "title_zh" not in item.meta
    assert item.evidence.quotes[0].translation == ""


def test_title_and_rule_quotes_get_renderings():
    item = make(quotes=[Quote(text="met the primary endpoint", locator="tag:ENDPOINT_MET")])
    client = FakeClient(["Phase 3 试验达到主要终点", "达到主要终点"])
    filled = translate.apply(item, translate.ClaudeTranslator(client=client))
    assert filled == 2
    assert item.meta["title_zh"] == "Phase 3 试验达到主要终点"
    assert item.evidence.quotes[0].translation == "达到主要终点"
    request = client.requests[0]
    assert request["model"] == "claude-opus-5"
    assert request["output_config"]["format"]["type"] == "json_schema"


def test_chinese_source_is_never_sent_for_rendering():
    item = make(title="某公司III期临床达到主要终点",
                quotes=[Quote(text="该试验达到主要终点")])
    client = FakeClient([])
    assert translate.apply(item, translate.ClaudeTranslator(client=client)) == 0
    assert client.requests == []


def test_existing_translation_from_the_significance_step_is_kept():
    item = make(quotes=[Quote(text="met the primary endpoint", translation="达到主要终点")])
    client = FakeClient(["标题译文"])
    translate.apply(item, translate.ClaudeTranslator(client=client))
    sent = json.loads(client.requests[0]["messages"][0]["content"].split("\n", 1)[1])
    assert [row["text"] for row in sent] == [item.title]
    assert item.evidence.quotes[0].translation == "达到主要终点"


def test_evaluative_rendering_is_dropped():
    item = make(quotes=[Quote(text="met the primary endpoint")])
    client = FakeClient(["Phase 3 试验达到主要终点", "达到主要终点，重磅利好"])
    translate.apply(item, translate.ClaudeTranslator(client=client))
    assert item.meta["title_zh"] == "Phase 3 试验达到主要终点"
    assert item.evidence.quotes[0].translation == ""


def test_non_chinese_or_echoed_output_is_dropped():
    item = make(quotes=[Quote(text="met the primary endpoint")])
    client = FakeClient(["Phase 3 trial met the primary endpoint", "met the primary endpoint"])
    assert translate.apply(item, translate.ClaudeTranslator(client=client)) == 0


def test_api_failure_leaves_originals_alone():
    class Broken:
        messages = SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(RuntimeError("down")))
    item = make(quotes=[Quote(text="met the primary endpoint")])
    assert translate.apply(item, translate.ClaudeTranslator(client=Broken())) == 0
    assert "title_zh" not in item.meta


def test_translate_items_counts_what_is_still_missing():
    items = [make(quotes=[Quote(text="met the primary endpoint")]),
             make(title="中文标题", quotes=[Quote(text="中文引文")])]
    filled, missing = translate.translate_items(items, translate.NullTranslator())
    assert (filled, missing) == (0, 2)


def test_builder_without_credentials_is_null(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr("os.path.isdir", lambda p: False)
    assert isinstance(translate.build_translator(), translate.NullTranslator)


def test_a_rendering_that_keeps_a_proper_noun_is_still_chinese():
    from liver_intel.translate import _accept
    assert _accept("Liquidation of MagIA Diagnostics", "MagIA Diagnostics 清算", "x") == "MagIA Diagnostics 清算"
    assert _accept("Liquidation of MagIA Diagnostics", "Liquidation of MagIA Diagnostics Ltd", "x") == ""


def test_a_rendering_that_loses_the_source_numbers_is_dropped():
    """The other checks ask whether it looks like a rendering, not whether it
    renders *this* text. A mis-keyed worksheet slot published an enrolment of
    "62 (ACTUAL)" as a sentence about why the sponsor stopped the trial."""
    assert translate._accept("62 (ACTUAL)", "研究提前关闭，因申办方决定不再继续开发。", "x") == ""
    assert translate._accept("425 adults were included.", "纳入了 425 名成人。", "x") \
        == "纳入了 425 名成人。"
    # A dropped or altered percentage is a defect in its own right.
    assert translate._accept("plaque in 81.6% vs 60.7%", "斑块 81.6% vs 60%", "x") == ""
    # No numbers to preserve: the other checks decide on their own.
    assert translate._accept("Liquidation of MagIA Diagnostics",
                             "MagIA Diagnostics 清算", "x") == "MagIA Diagnostics 清算"
