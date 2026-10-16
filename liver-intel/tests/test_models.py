import pytest

from liver_intel.models import (ContractError, Item, Quote, as_iso_date,
                                canonical_url, dedup, validate, validate_quotes)


def make(**kwargs):
    base = dict(src="newswire", title="t", url="https://www.example.com/a",
                date="2026-09-14", meta={"src_kind": "company"})
    base.update(kwargs)
    return Item(**base)


def test_valid_item_passes():
    assert validate(make()) is not None


def test_item_without_url_is_rejected():
    with pytest.raises(ContractError, match="no source URL"):
        validate(make(url=""))


@pytest.mark.parametrize("url", [
    "https://endpts.com/story",
    "https://www.fiercebiotech.com/x",
    "https://www.medrxiv.org/content/10.1101/2026.01.01",
    "https://www.biorxiv.org/x",
])
def test_media_and_preprints_are_rejected(url):
    with pytest.raises(ContractError, match="media relay or preprint"):
        validate(make(url=url))


def test_unknown_src_kind_is_rejected():
    with pytest.raises(ContractError, match="src_kind"):
        validate(make(meta={"src_kind": "blog"}))


def test_date_must_be_iso():
    with pytest.raises(ContractError, match="ISO"):
        validate(make(date="14/09/2026"))


def test_json_roundtrip_keeps_contract_fields():
    item = make()
    item.evidence.signals = ["PH3_RESULT"]
    item.evidence.quotes = [Quote(text="met the primary endpoint", locator="para 1")]
    payload = item.to_json()
    assert list(payload) == ["src", "title", "url", "date", "meta", "lines",
                            "study", "P", "why", "score", "evidence"]
    restored = Item.from_json(payload)
    assert restored.evidence.quotes[0].text == "met the primary endpoint"


def test_canonical_url_strips_tracking():
    assert canonical_url("https://Example.com/a/?utm_source=x&id=3") == \
        "https://example.com/a?id=3"


def test_dedup_keeps_first():
    a, b = make(title="first"), make(title="second")
    assert [i.title for i in dedup([a, b])] == ["first"]


def test_quotes_must_appear_in_source():
    item = make()
    item.evidence.quotes = [Quote(text="really said"), Quote(text="never said")]
    missing = validate_quotes(item, "the company really said something")
    assert [q.text for q in missing] == ["never said"]


@pytest.mark.parametrize("raw,expected", [
    ("2026-09-14T10:00:00Z", "2026-09-14"),
    ("Mon, 14 Sep 2026 08:00:00 GMT", "2026-09-14"),
    ("2026/09/14", "2026-09-14"),
    ("20260914", "2026-09-14"),
    ("2026年09月14日", "2026-09-14"),
    ("nonsense", None),
])
def test_date_parsing(raw, expected):
    assert as_iso_date(raw) == expected
