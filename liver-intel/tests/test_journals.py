"""The journal roster: weight, not collection."""
from __future__ import annotations

import json

import pytest

from liver_intel.grade import rule_signals
from liver_intel.journals import Roster
from liver_intel.models import Item


@pytest.fixture
def roster(tmp_path):
    path = tmp_path / "journals.json"
    path.write_text(json.dumps({
        "operator_confirmed": False,
        "tiers": {
            "A": {"journals": [{"name": "Journal of hepatology", "abbrev": "J Hepatol"},
                               {"name": "Hepatology (Baltimore, Md.)", "abbrev": "Hepatology"}]},
            "B": {"journals": [{"name": "Hepatology communications", "abbrev": "Hepatol Commun"}]},
        }}, ensure_ascii=False), encoding="utf-8")
    return Roster.load(path)


def test_the_catalogue_suffix_does_not_break_a_match(roster):
    """NLM writes 'Hepatology (Baltimore, Md.)' and 'Clinical gastroenterology
    and hepatology : the official ...'; the roster must not have to repeat those."""
    assert roster.tier("Hepatology (Baltimore, Md.)") == "A"
    assert roster.tier("Hepatology") == "A"
    assert roster.tier("Journal of hepatology") == "A"
    assert roster.tier("J Hepatol") == "A"


def test_a_lookalike_title_is_not_the_journal(roster):
    """'Hepatology communications' must not match the 'Hepatology' entry."""
    assert roster.tier("Hepatology communications") == "B"


def test_a_sibling_title_does_not_inherit_the_family_name(roster, tmp_path):
    """A prefix rule looks right until it puts 'Nature structural & molecular
    biology' in the same tier as 'Nature' -- it graded a cryo-EM paper as a
    clinical headline before the match was made exact."""
    path = tmp_path / "n.json"
    path.write_text(json.dumps({"tiers": {"A": {"journals": [{"name": "Nature"}]}}}),
                    encoding="utf-8")
    nature = Roster.load(path)
    assert nature.tier("Nature") == "A"
    assert nature.tier("Nature (London)") == "A"          # catalogue qualifier
    assert nature.tier("Nature structural & molecular biology") == ""
    assert nature.tier("Nature reviews. Gastroenterology & hepatology") == ""


def test_a_journal_off_the_roster_has_no_tier(roster):
    assert roster.tier("Frontiers in endocrinology") == ""
    assert roster.tier("") == ""


def test_the_shipped_roster_carries_the_operator_s_five():
    named = [j["abbrev"] for j in json.loads(
        (__import__("liver_intel.config", fromlist=["DATA_DIR"]).DATA_DIR
         / "journals_liver.json").read_text(encoding="utf-8"))["tiers"]["A"]["journals"]
        if j.get("named_by_operator")]
    assert set(named) == {"J Hepatol", "Hepatology", "Gut",
                          "Lancet Gastroenterol Hepatol", "N Engl J Med"}


# --- grading ---------------------------------------------------------------
def paper(journal, study, title="A trial in MASH"):
    return Item(src="pubmed", title=title, url="https://pubmed.ncbi.nlm.nih.gov/1/",
                date="2026-09-17", lines=["L3"], study=list(study),
                meta={"src_kind": "journal", "journal": journal,
                      "structural_study": list(study)})


def test_a_tier_a_clinical_result_is_a_soft_signal():
    item = paper("Journal of hepatology", ["PUBLICATION", "PHASE3", "ENDPOINT_MET"])
    assert "JOURNAL_PIVOTAL" in rule_signals(item)
    assert item.meta["journal_tier"] == "A"


def test_a_tier_a_review_is_only_background():
    item = paper("Gut", ["PUBLICATION"], title="Review of MASH pathogenesis")
    signals = rule_signals(item)
    assert "JOURNAL_MAJOR" in signals and "JOURNAL_PIVOTAL" not in signals


def test_a_tier_b_paper_needs_a_clinical_result_to_count():
    assert "JOURNAL_MAJOR" in rule_signals(
        paper("Hepatology communications", ["PUBLICATION", "REAL_WORLD"]))
    assert rule_signals(paper("Hepatology communications", ["PUBLICATION"])) == []


def test_a_journal_off_the_roster_fires_nothing():
    assert rule_signals(paper("Frontiers in endocrinology", ["PUBLICATION", "PHASE3"])) == \
        ["PIVOTAL_PUBLICATION"]


def test_the_roster_never_applies_to_a_company_release():
    item = paper("Journal of hepatology", ["PUBLICATION", "PHASE3"])
    item.meta["src_kind"] = "company"
    assert "JOURNAL_PIVOTAL" not in rule_signals(item)
