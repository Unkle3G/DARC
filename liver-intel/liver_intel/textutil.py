"""Small text helpers shared by the adapters."""
from __future__ import annotations

import re
from html.parser import HTMLParser

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
