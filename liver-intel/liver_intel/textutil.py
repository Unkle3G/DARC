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
