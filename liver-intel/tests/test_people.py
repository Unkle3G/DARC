from liver_intel.models import Item
from liver_intel.people import authors_from_pubmed_xml, issuer_contributors

PUBMED_XML = """<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>40123456</PMID>
<Article><AuthorList>
<Author><LastName>Loomba</LastName><ForeName>Rohit</ForeName>
  <Identifier Source="ORCID">0000-0002-1111-2222</Identifier>
  <AffiliationInfo><Affiliation>UC San Diego, La Jolla, CA, USA</Affiliation></AffiliationInfo></Author>
<Author><LastName>Zhang</LastName><ForeName>Wei</ForeName>
  <AffiliationInfo><Affiliation>Beijing Friendship Hospital, Capital Medical University</Affiliation></AffiliationInfo></Author>
<Author><CollectiveName>The MAESTRO Investigators</CollectiveName></Author>
</AuthorList></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"""


def test_author_order_is_preserved():
    people = authors_from_pubmed_xml(PUBMED_XML)["40123456"]
    assert [p["position"] for p in people] == [1, 2, 3]
    assert people[0]["name"] == "Rohit Loomba"
    assert people[1]["name"] == "Wei Zhang"


def test_affiliation_and_orcid_are_captured():
    first = authors_from_pubmed_xml(PUBMED_XML)["40123456"][0]
    assert "UC San Diego" in first["affiliation"]
    assert first["orcid"] == "0000-0002-1111-2222"


def test_collective_author_is_kept():
    assert authors_from_pubmed_xml(PUBMED_XML)["40123456"][2]["name"] == \
        "The MAESTRO Investigators"


def test_malformed_xml_returns_nothing():
    assert authors_from_pubmed_xml("<not xml") == {}


def test_issuer_recorded_at_position_zero():
    people = issuer_contributors({"companies": ["Madrigal Pharmaceuticals"]})
    assert people[0]["position"] == 0
    assert people[0]["role"] == "issuer"


def test_media_contact_is_extracted_without_swallowing_the_email():
    people = issuer_contributors({"companies": ["Acme"]},
                                 "Media Contact:\nJane Doe\njane@example.com")
    assert [p["name"] for p in people] == ["Acme", "Jane Doe"]


def test_no_contact_line_yields_only_the_issuer():
    people = issuer_contributors({"companies": ["Acme"]}, "no contact details here")
    assert [p["name"] for p in people] == ["Acme"]


# --- store round-trip -----------------------------------------------------
def journal_item():
    return Item(src="pubmed", title="MASH trial results",
                url="https://pubmed.ncbi.nlm.nih.gov/40123456/", date="2026-09-14",
                meta={"src_kind": "journal", "journal": "Journal of Hepatology",
                      "contributors": authors_from_pubmed_xml(PUBMED_XML)["40123456"]})


def test_contributors_are_persisted(store):
    assert store.record_contributors(journal_item()) == 3
    rows = store.contributors()
    assert len(rows) == 3
    assert rows[0]["publisher"] == "Journal of Hepatology"


def test_first_author_filter(store):
    store.record_contributors(journal_item())
    rows = store.contributors(first_only=True)
    assert [r["name"] for r in rows] == ["Rohit Loomba"]


def test_affiliation_search(store):
    store.record_contributors(journal_item())
    rows = store.contributors(affiliation="Beijing")
    assert [r["name"] for r in rows] == ["Wei Zhang"]


def test_recording_twice_does_not_duplicate(store):
    item = journal_item()
    store.record_contributors(item)
    store.record_contributors(item)
    assert len(store.contributors()) == 3


def test_summary_counts_first_authorships(store):
    store.record_contributors(journal_item())
    summary = {row["name"]: row for row in store.contributor_summary()}
    assert summary["Rohit Loomba"]["first_author"] == 1
    assert summary["Wei Zhang"]["first_author"] == 0


def test_contact_name_found_across_a_blank_line():
    """html_to_text puts a blank line between paragraphs."""
    people = issuer_contributors({"companies": ["Acme"]},
                                 "Media Contact:\n\nJane Doe\n\njane@example.com")
    assert [p["name"] for p in people] == ["Acme", "Jane Doe"]
