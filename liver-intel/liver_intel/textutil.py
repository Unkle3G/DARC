"""Small text helpers shared by the adapters."""
from __future__ import annotations

import logging
import re
from html.parser import HTMLParser

log = logging.getLogger(__name__)

_SKIP_TAGS = {"script", "style", "noscript", "svg", "head"}
_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
               "section", "article", "td"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Flatten markup to readable text, preserving paragraph breaks.

    Quotes cited by the significance step are matched against this output, so it
    must stay stable: same input, same characters.
    """
    parser = _TextExtractor()
    try:
        parser.feed(html or "")
    except Exception:
        parser.parts.append(re.sub(r"<[^>]+>", " ", html or ""))
    text = "".join(parser.parts)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n\s*\n\s*", "\n\n", text)
    return text.strip()


def clip(text: str, limit: int = 4000) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "\n[truncated]"


#: Where a press release stops reporting and starts repeating itself. Everything
#: from here on is legal and marketing furniture that appears in every release:
#: the forward-looking-statements disclaimer names approvals and trials for
#: legal reasons, and the "About <product>" block recaps the whole programme.
#: Quoting either as evidence of today's news is simply wrong -- one real filing
#: announced a board appointment and was graded a Phase 3 readout on the
#: strength of its boilerplate.
_BOILERPLATE = re.compile(
    r"(?im)^\s*(?:"
    r"forward[- ]looking statements?"
    r"|safe harbou?r statement"
    r"|cautionary (?:note|statement)"
    r"|about\s+\S[^\n]{0,60}$"
    r"|investor (?:relations|contacts?|inquiries)"
    r"|media (?:relations|contacts?|inquiries)"
    r"|press contacts?"
    r"|source:\s"
    r"|#\s*#\s*#"
    r")")

#: Exhibit wrappers EDGAR prepends before the release itself.
_EXHIBIT_PREAMBLE = re.compile(
    r"(?im)^\s*(?:ex-?99[.\d]*|exhibit\s+99[.\d]*|press release|\d{1,2}|[\w.\-]+\.html?)\s*$")


def strip_boilerplate(text: str) -> str:
    """The reporting part of a release, with legal and marketing blocks removed."""
    match = _BOILERPLATE.search(text or "")
    return (text[: match.start()] if match else (text or "")).strip()


def lead(text: str, limit: int = 600) -> str:
    """Headline plus opening paragraphs -- what the document announces.

    A press release states its news at the top; what appears further down is
    recap and background. Grading reads this, not the whole document, so a
    quarterly report does not inherit every event it mentions in passing.
    """
    body = strip_boilerplate(text)
    lines = [line for line in body.splitlines()
             if line.strip() and not _EXHIBIT_PREAMBLE.match(line)]
    out: list[str] = []
    size = 0
    for line in lines:
        out.append(line.strip())
        size += len(line)
        if size >= limit:
            break
    return "\n".join(out)


def pdf_text(data: bytes, max_pages: int = 6) -> str:
    """Text of a PDF announcement.

    HKEX publishes announcements as PDFs whose search headline is only a
    category label ("Announcements and Notices - [Other - Business Update]"),
    so the document itself is the only place the news exists.
    """
    import io
    import logging as _logging

    try:
        import pypdf
    except ImportError:
        log.info("pypdf is not installed; PDF announcements cannot be read")
        return ""
    # pypdf warns per font about optional tooling; it is noise here.
    _logging.getLogger("pypdf").setLevel(_logging.ERROR)
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        pages = reader.pages[:max_pages]
        return re.sub(r"\n{3,}", "\n\n",
                      "\n".join((page.extract_text() or "") for page in pages)).strip()
    except Exception as exc:
        log.info("could not read PDF: %s", exc)
        return ""


#: Abbreviations whose full stop does not end a sentence. Results prose is full
#: of them -- "reduced injurious falls (4% vs. 12%)" was being cut in half at
#: "vs.", and a quote is published verbatim, so half a sentence ships as the
#: evidence for a signal.
_ABBREVIATIONS = frozenset("""
vs v.s cf e.g i.e etc al no nos fig figs eq eqs ref refs approx ca est
vol pp p pt ch sec dr mr mrs ms prof st jr sr inc ltd co corp
""".split())

#: A newline always ends a sentence (abstracts are section-per-line); a full
#: stop ends one only when the token in front of it is not an abbreviation.
_BOUNDARY = re.compile(r"\n+|(?<=[.!?\u3002\uff01\uff1f])[ \t]+")
_TRAILING_TOKEN = re.compile(r"([A-Za-z][A-Za-z.]*)\.$")


def split_sentences(text: str) -> list[str]:
    """Sentences, verbatim, without splitting inside an abbreviation."""
    text = text or ""
    parts: list[str] = []
    start = 0
    for match in _BOUNDARY.finditer(text):
        head = text[start:match.start()]
        if not match.group(0).startswith("\n"):
            token = _TRAILING_TOKEN.search(head.rstrip())
            if token and token.group(1).strip(".").lower() in _ABBREVIATIONS:
                continue
        parts.append(head.strip())
        start = match.end()
    parts.append(text[start:].strip())
    return [part for part in parts if len(part) > 12]


#: A structured abstract labels its own sections. PubMed keeps the labels, so
#: "where does this paper say what it found" needs no judgement -- it is the
#: first sentence under RESULTS and under CONCLUSIONS.
_SECTION = re.compile(r"(?m)^[ \t]*([A-Z][A-Z0-9 /&'()-]{2,40}):[ \t]*")

#: Label -> which of the two findings slots it fills. DISCUSSION is a fallback:
#: some journals use it where others write CONCLUSIONS, but CONCLUSIONS wins
#: when a paper carries both.
_RESULT_LABELS = ("RESULT", "FINDING")
_CONCLUSION_LABELS = (("CONCLUSION", "INTERPRETATION"), ("DISCUSSION",))


def abstract_findings(text: str) -> list[tuple[str, str]]:
    """(label, first sentence) for the RESULTS and CONCLUSIONS sections.

    Empty for an unstructured abstract, which is the honest answer: without the
    labels there is no rule that reliably finds the finding, and a guess would
    ship as a verbatim quote. Those are left to the judgement worksheet.
    """
    matches = list(_SECTION.finditer(text or ""))
    if not matches:
        return []
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append((match.group(1).upper(), text[match.end():end].strip()))

    def pick(predicate, which: int) -> tuple[str, str] | None:
        for label, body in sections:
            if predicate(label):
                sentences = split_sentences(body)
                if sentences:
                    return (label, sentences[which])
        return None

    out: list[tuple[str, str]] = []
    # RESULTS opens on the baseline -- "MASLD was present in 47.3% of
    # participants", "192 patients were studied" -- and closes on the finding
    # the section was written to deliver, so the last sentence is the one worth
    # quoting. CONCLUSIONS is the other way round: its first sentence states the
    # claim and the rest qualifies it.
    found = pick(lambda label: any(word in label for word in _RESULT_LABELS), -1)
    if found:
        out.append(found)
    for group in _CONCLUSION_LABELS:
        found = pick(lambda label: any(word in label for word in group), 0)
        if found:
            out.append(found)
            break
    return out
