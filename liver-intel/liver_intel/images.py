"""Candidate images for an item.

Press releases carry their own figures, and the handover allows using what the
first-party document itself publishes. Two rules apply:

* Only images from the source document are ever considered -- nothing is pulled
  from a search engine or a media site.
* The image is a *candidate*, carrying its source URL. Rights are the operator's
  call, which is why ``report_wechat`` prints the source next to every figure.

WeChat's editor will not hot-link a remote image: it has to be uploaded to the
account's own library. ``download`` therefore saves the bytes locally so the
operator has files to upload, and the HTML points at those local paths.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

log = logging.getLogger(__name__)

#: Filenames that are almost always furniture rather than content.
_FURNITURE = re.compile(
    r"(?i)(logo|icon|favicon|sprite|avatar|badge|button|banner[-_]?ad|"
    r"pixel|spacer|tracking|1x1|beacon|footer|header[-_]?bg)")

_ALLOWED_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")


@dataclass
class ImageCandidate:
    url: str
    alt: str = ""
    source_url: str = ""
    role: str = "body"          # "social" for og:image/twitter:image
    width: int | None = None
    height: int | None = None
    local_path: str = ""

    def to_json(self) -> dict[str, Any]:
        out = {"url": self.url, "role": self.role}
        for key in ("alt", "source_url", "local_path"):
            if getattr(self, key):
                out[key] = getattr(self, key)
        for key in ("width", "height"):
            if getattr(self, key):
                out[key] = getattr(self, key)
        return out


class _ImageScraper(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.social: list[tuple[str, str]] = []
        self.body: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            content = values.get("content", "")
            if content and key in ("og:image", "og:image:url", "twitter:image"):
                self.social.append((content, values.get("alt", "")))
        elif tag == "img":
            src = values.get("src") or values.get("data-src") or ""
            if src:
                self.body.append({"src": src, "alt": values.get("alt", ""),
                                  "width": values.get("width", ""),
                                  "height": values.get("height", "")})


def _as_int(value: str) -> int | None:
    try:
        return int(re.sub(r"[^\d]", "", value) or 0) or None
    except ValueError:
        return None


def _plausible(url: str, width: int | None, height: int | None) -> bool:
    if url.startswith("data:"):
        return False
    path = urlparse(url).path.lower()
    if path.endswith(".svg"):
        return False
    if path and not path.endswith(_ALLOWED_EXT) and "." in path.rsplit("/", 1)[-1]:
        return False
    if _FURNITURE.search(url):
        return False
    if (width and width < 200) or (height and height < 150):
        return False
    return True


def extract_images(html: str, base_url: str, limit: int = 3) -> list[ImageCandidate]:
    """Content images published by the document itself, best first."""
    parser = _ImageScraper()
    try:
        parser.feed(html or "")
    except Exception as exc:
        log.debug("image scrape failed for %s: %s", base_url, exc)
        return []

    out: list[ImageCandidate] = []
    seen: set[str] = set()

    for src, alt in parser.social:
        url = urljoin(base_url, src)
        if url in seen or not _plausible(url, None, None):
            continue
        seen.add(url)
        out.append(ImageCandidate(url=url, alt=alt, source_url=base_url, role="social"))

    for entry in parser.body:
        url = urljoin(base_url, entry["src"])
        width, height = _as_int(entry["width"]), _as_int(entry["height"])
        if url in seen or not _plausible(url, width, height):
            continue
        seen.add(url)
        out.append(ImageCandidate(url=url, alt=entry["alt"], source_url=base_url,
                                  width=width, height=height))
    return out[:limit]


def download(candidates: list[ImageCandidate], fetcher: Any, out_dir: Path,
             max_bytes: int = 8_000_000) -> list[ImageCandidate]:
    """Save image bytes locally so they can be uploaded to the WeChat editor.

    Failures are not fatal: the candidate keeps its remote URL and the renderer
    falls back to that, noting the image could not be retrieved.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    session = getattr(fetcher, "session", None)
    for index, candidate in enumerate(candidates):
        try:
            if session is None:
                raise RuntimeError("fetcher exposes no binary session")
            response = session.get(candidate.url, timeout=30)
            response.raise_for_status()
            content = response.content
            if len(content) > max_bytes:
                log.info("image too large, skipped: %s", candidate.url)
                continue
            suffix = Path(urlparse(candidate.url).path).suffix.lower() or ".jpg"
            if suffix not in _ALLOWED_EXT:
                suffix = ".jpg"
            path = out_dir / f"{index:02d}{suffix}"
            path.write_bytes(content)
            candidate.local_path = str(path)
        except Exception as exc:
            log.info("image download failed for %s: %s", candidate.url, exc)
    return candidates


def from_meta(meta: dict[str, Any]) -> list[ImageCandidate]:
    out = []
    for raw in meta.get("images") or []:
        out.append(ImageCandidate(**{k: v for k, v in raw.items()
                                     if k in ImageCandidate.__dataclass_fields__}))
    return out
