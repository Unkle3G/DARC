"""Chinese renderings for a Chinese-reading audience.

The reader-facing article shows every non-Chinese title and quote in its source
language, followed by a Chinese rendering marked 编者译，仅供参考. The significance
step already translates the quotes it finds; this step covers the rest of what
ships -- the title, and the sentences the deterministic rules quoted -- so no
English-only entry reaches the article when a model is configured.

Rules, the same as the significance step's:

* translate only what is written -- no background, no gloss, no evaluation;
* a Chinese source is never translated (``models.is_chinese``);
* without a credential nothing is invented: the original stands alone and the
  internal report says so.

Only the items selected for the day are translated, which bounds the cost at
the daily cap.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Protocol

from .llm import EvaluativeLanguage, assert_not_evaluative, credential_status
from .models import Item, _CJK, is_chinese

log = logging.getLogger(__name__)

MODEL = os.environ.get("LIVER_INTEL_MODEL", "claude-opus-5")

SYSTEM_PROMPT = """\
You translate excerpts from pharmaceutical and clinical documents into Chinese \
for an editorial team. You are a translator, not an analyst.

Rules:
- Translate each text literally and completely into Simplified Chinese.
- Keep drug codes, compound names, trial names, company names, gene and target \
names, registry identifiers and journal names exactly as written in the source.
- Add nothing: no background, no explanation, no terminology gloss, no \
parenthetical notes, no evaluation of what the text says.
- Do not predict, rank, or describe anything as positive, negative, important \
or encouraging.
- Return one translation per input, in the same order, keyed by index.
"""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "zh": {"type": "string"},
                },
                "required": ["index", "zh"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["translations"],
    "additionalProperties": False,
}


class Translator(Protocol):
    def translate(self, texts: list[str]) -> list[str]: ...


class NullTranslator:
    """No model: every rendering is empty and the original stands alone."""

    def translate(self, texts: list[str]) -> list[str]:
        return ["" for _ in texts]


class ClaudeTranslator:
    """Literal renderings from the Anthropic API, one request per item."""

    def __init__(self, model: str = MODEL, client: Any | None = None):
        self.model = model
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def translate(self, texts: list[str]) -> list[str]:
        if not texts:
            return []
        payload = json.dumps(
            [{"index": i, "text": t} for i, t in enumerate(texts)], ensure_ascii=False)
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8000,
                system=SYSTEM_PROMPT,
                thinking={"type": "adaptive"},
                output_config={
                    "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
                    "effort": "low",
                },
                messages=[{"role": "user", "content":
                           f"Translate each `text` into Chinese:\n{payload}"}],
            )
        except Exception as exc:  # network, auth, rate limit -- the original stands
            log.warning("translation failed: %s", exc)
            return ["" for _ in texts]
        if getattr(response, "stop_reason", None) == "refusal":
            log.warning("translation refused")
            return ["" for _ in texts]
        try:
            rows = json.loads(_response_text(response)).get("translations", [])
        except (json.JSONDecodeError, AttributeError) as exc:
            log.warning("translation returned unparseable output: %s", exc)
            return ["" for _ in texts]
        out = ["" for _ in texts]
        for row in rows:
            try:
                index = int(row.get("index"))
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(out):
                out[index] = str(row.get("zh") or "").strip()
        return out


def _response_text(response: Any) -> str:
    parsed = getattr(response, "parsed_output", None)
    if parsed is not None:
        return json.dumps(parsed if isinstance(parsed, dict) else parsed.__dict__)
    return "".join(block.text for block in getattr(response, "content", []) or []
                   if getattr(block, "type", None) == "text").strip()


def build_translator(enabled: bool = True) -> Translator:
    """Claude when a credential is configured, otherwise nothing is translated."""
    if not enabled or credential_status() is not None:
        return NullTranslator()
    return ClaudeTranslator()


def needs_rendering(text: str) -> bool:
    return bool((text or "").strip()) and not is_chinese(text)


def _accept(source: str, rendering: str, where: str) -> str:
    """A rendering is kept only when it is Chinese, differs from its source and
    carries no judgement. Anything else is dropped rather than published."""
    rendering = (rendering or "").strip()
    # "Contains Chinese", not "mostly Chinese": a rendering that keeps a proper
    # noun as written ("MagIA Diagnostics 清算") is dominated by Latin letters
    # by design, and the ratio test rejected exactly the renderings that
    # followed the rules best.
    if not rendering or rendering == source.strip() or not _CJK.search(rendering):
        return ""
    try:
        assert_not_evaluative(rendering, where)
    except EvaluativeLanguage as exc:
        log.warning("dropped a rendering: %s", exc)
        return ""
    return rendering


def apply(item: Item, translator: Translator) -> int:
    """Fill ``meta.title_zh`` and each quote's ``translation``; return how many."""
    slots: list[tuple[str, Any]] = []
    if needs_rendering(item.title) and not item.meta.get("title_zh"):
        slots.append(("title", item.title))
    for quote in item.evidence.quotes:
        if needs_rendering(quote.text) and not quote.translation:
            slots.append(("quote", quote))
    if not slots:
        return 0
    texts = [item.title if kind == "title" else target.text for kind, target in slots]
    renderings = translator.translate(texts)
    filled = 0
    for (kind, target), rendering in zip(slots, renderings):
        if kind == "title":
            accepted = _accept(item.title, rendering, "title rendering")
            if accepted:
                item.meta["title_zh"] = accepted
                filled += 1
        else:
            accepted = _accept(target.text, rendering, "quote rendering")
            if accepted:
                target.translation = accepted
                filled += 1
    return filled


def translate_items(items: list[Item], translator: Translator) -> tuple[int, int]:
    """(renderings filled, renderings still missing) across the items."""
    filled = 0
    for item in items:
        filled += apply(item, translator)
    missing = sum(
        (1 if needs_rendering(i.title) and not i.meta.get("title_zh") else 0)
        + sum(1 for q in i.evidence.quotes if needs_rendering(q.text) and not q.translation)
        for i in items)
    return filled, missing
