"""Contributor extraction for the materials library.

Journal records carry real authorship (first author, second author, each with an
affiliation); a company release does not, so its issuer and media contact are
recorded instead with position 0. Everything here feeds
``Store.record_contributors`` and is never shown to readers.
"""
from __future__ import annotations

import logging
import re
from typing import Any
from xml.etree import ElementTree

log = logging.getLogger(__name__)

#: The label is case-insensitive, the name is not -- re.I would let `[A-Z]`
#: match lowercase and swallow the email address on the next line.
_CONTACT = re.compile(
    r"(?i:(?:media|press|investor|ir)\s*(?:relations|contact|inquiries))\s*[:：]?"
    r"[ \t]*\n{0,2}[ \t]*"   # the name usually sits on its own line, after a blank one
    r"([A-Z][A-Za-z.'\-]+(?:[ \t]+[A-Z][A-Za-z.'\-]+){1,2})")


def authors_from_pubmed_xml(xml_text: str) -> dict[str, list[dict[str, Any]]]:
    """Parse an efetch PubMed XML batch into ``{pmid: [contributor, ...]}``.

    esummary gives names but no affiliations, which is why the adapter makes the
    extra efetch call: the affiliation is the point of the library.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        log.warning("pubmed efetch XML did not parse: %s", exc)
        return out

    for article in root.iter("PubmedArticle"):
        pmid_node = article.find("./MedlineCitation/PMID")
        if pmid_node is None or not (pmid_node.text or "").strip():
            continue
        pmid = pmid_node.text.strip()
        people: list[dict[str, Any]] = []
        for position, author in enumerate(
                article.iterfind("./MedlineCitation/Article/AuthorList/Author"), start=1):
            name = _author_name(author)
            if not name:
                continue
            affiliation = ""
            node = author.find("./AffiliationInfo/Affiliation")
            if node is not None and node.text:
                affiliation = node.text.strip()
            orcid = ""
            for identifier in author.iterfind("./Identifier"):
                if (identifier.get("Source") or "").upper() == "ORCID" and identifier.text:
                    orcid = identifier.text.strip()
            people.append({
                "position": position,
                "name": name,
                "affiliation": affiliation,
                "role": "author",
                "orcid": orcid,
            })
        if people:
            out[pmid] = people
    return out


def _author_name(author: ElementTree.Element) -> str:
    collective = author.find("./CollectiveName")
    if collective is not None and collective.text:
        return collective.text.strip()
    last = (author.findtext("./LastName") or "").strip()
    fore = (author.findtext("./ForeName") or author.findtext("./Initials") or "").strip()
    return f"{fore} {last}".strip() if last else fore


def issuer_contributors(meta: dict[str, Any], body: str = "") -> list[dict[str, Any]]:
    """Position-0 contributors for a document with no byline."""
    people: list[dict[str, Any]] = []
    for company in (meta.get("companies") or [])[:3]:
        people.append({"position": 0, "name": str(company), "role": "issuer",
                       "affiliation": str(company)})
    regulator = meta.get("regulator")
    if regulator:
        people.append({"position": 0, "name": str(regulator), "role": "regulator",
                       "affiliation": str(regulator)})
    sponsor = meta.get("sponsor")
    if sponsor:
        people.append({"position": 0, "name": str(sponsor), "role": "sponsor",
                       "affiliation": str(sponsor)})
    match = _CONTACT.search(body or "")
    if match:
        people.append({"position": 0, "name": match.group(1).strip(),
                       "role": "media_contact",
                       "affiliation": str((meta.get("companies") or [""])[0])})
    seen: set[tuple[int, str]] = set()
    unique = []
    for person in people:
        key = (person["position"], person["name"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(person)
    return unique
