"""LLM significance step (task sheet section 0).

The model's only job is to say **which signals fired and which sentences of the
source say so**.  It does not rate, forecast or contextualise.  Three guards
enforce that:

1. The response is constrained to a JSON schema whose ``signal`` field is an
   enum of :data:`liver_intel.grade.SIGNALS`.
2. Every quote must appear verbatim in the source text, or it is dropped.
3. A signal with no surviving quote is dropped -- "命中信号 + 原文依据" means a
   signal without its evidence is not a finding.

With no API key configured the pipeline runs on deterministic rules alone
(:class:`NullJudge`), so nothing here is load-bearing for a dry run.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from .grade import SIGNALS
from .models import Item, Quote, _normalise_ws

log = logging.getLogger(__name__)

MODEL = os.environ.get("LIVER_INTEL_MODEL", "claude-opus-5")

SYSTEM_PROMPT = """\
You are an extraction step in a liver-disease intelligence pipeline. You are \
not an analyst and you do not give opinions.

Given one source document, decide which of the listed signals the document \
states, and quote the exact sentence(s) that state each one.

Rules:
- Quote verbatim from the document. Never paraphrase inside `text`. Copy the \
characters exactly, including punctuation.
- If the document is in Chinese, keep `text` in the original Chinese and put a \
literal translation in `translation`. Translate only what is written: add no \
background, no explanation, no interpretation.
- Report only what the document itself states. Do not use outside knowledge \
about the company, the drug or the field.
- Do not predict, evaluate, rank, or describe anything as positive, negative, \
important, disappointing or encouraging.
- If a signal is not clearly stated in the document, leave it out. Returning an \
empty list is correct and expected for routine documents.
"""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "signal": {"type": "string", "enum": sorted(SIGNALS)},
                    "quotes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "locator": {"type": "string"},
                                "lang": {"type": "string"},
                                "translation": {"type": "string"},
                            },
                            "required": ["text", "locator", "lang", "translation"],
                            "additionalProperties": False,
                        },
                        "minItems": 1,
                    },
                },
                "required": ["signal", "quotes"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}

#: Words that turn an extraction into a judgement.  Checked against text the
#: engine emits (translations, ``why``), never against the source itself.
EVALUATIVE_MARKERS = (
    "利好", "利空", "看好", "预计将", "有望", "值得关注", "重磅", "突破性进展",
    "领先于", "优于竞品", "强劲", "令人失望", "超预期", "不及预期",
    "likely to", "we expect", "impressive", "disappointing", "strong result",
    "beats", "misses expectations", "promising", "encouraging", "setback",
    "game-chang", "best-in-class", "blockbuster",
)


class EvaluativeLanguage(ValueError):
    pass


def assert_not_evaluative(text: str, where: str = "") -> None:
    lowered = (text or "").lower()
    for marker in EVALUATIVE_MARKERS:
        if marker.lower() in lowered:
            raise EvaluativeLanguage(
                f"evaluative wording {marker!r} in {where or 'generated text'}")


@dataclass
class JudgeResult:
    signals: list[str] = field(default_factory=list)
    quotes: list[Quote] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    raw: dict[str, Any] | None = None


class Judge(Protocol):
    def judge(self, item: Item, source_text: str) -> JudgeResult: ...


class NullJudge:
    """No model call: rule signals only.  Used for dry runs and tests."""

    def judge(self, item: Item, source_text: str) -> JudgeResult:  # noqa: ARG002
        return JudgeResult()


def _verify(findings: list[dict[str, Any]], source_text: str) -> JudgeResult:
    """Keep only signals whose quotes are really in the document."""
    haystack = _normalise_ws(source_text)
    result = JudgeResult(raw={"findings": findings})
    for finding in findings:
        signal = finding.get("signal")
        if signal not in SIGNALS:
            result.dropped.append(f"{signal}: not in the signal vocabulary")
            continue
        kept: list[Quote] = []
        for raw_quote in finding.get("quotes") or []:
            text = (raw_quote.get("text") or "").strip()
            if not text:
                continue
            if _normalise_ws(text) not in haystack:
                result.dropped.append(f"{signal}: quote not found in source")
                continue
            translation = (raw_quote.get("translation") or "").strip()
            if translation:
                try:
                    assert_not_evaluative(translation, f"{signal} translation")
                except EvaluativeLanguage as exc:
                    result.dropped.append(f"{signal}: {exc}")
                    translation = ""
            kept.append(Quote(text=text, locator=(raw_quote.get("locator") or "").strip(),
                              lang=(raw_quote.get("lang") or "").strip(),
                              translation=translation))
        if not kept:
            result.dropped.append(f"{signal}: dropped, no verifiable quote")
            continue
        if signal not in result.signals:
            result.signals.append(signal)
        result.quotes.extend(kept)
    return result


class ClaudeJudge:
    """Significance step backed by the Anthropic API."""

    def __init__(self, model: str = MODEL, client: Any | None = None,
                 max_source_chars: int = 60_000):
        self.model = model
        self.max_source_chars = max_source_chars
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def _prompt(self, item: Item, source_text: str) -> str:
        catalogue = "\n".join(
            f"- {name}: {spec['desc']}" for name, spec in sorted(SIGNALS.items()))
        body = source_text[: self.max_source_chars]
        if len(source_text) > self.max_source_chars:
            # Never silently truncate mid-analysis without saying so.
            body += "\n[document truncated for length]"
        return (
            f"Signals:\n{catalogue}\n\n"
            f"Document source: {item.src}\nURL: {item.url}\n"
            f"Title: {item.title}\n\n"
            f"Document text:\n<document>\n{body}\n</document>"
        )

    def judge(self, item: Item, source_text: str) -> JudgeResult:
        if not (source_text or "").strip():
            return JudgeResult()
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4000,
                system=SYSTEM_PROMPT,
                thinking={"type": "adaptive"},
                output_config={
                    "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
                    "effort": "medium",
                },
                messages=[{"role": "user", "content": self._prompt(item, source_text)}],
            )
        except Exception as exc:  # network, auth, rate limit -- rules still stand
            log.warning("significance step failed for %s: %s", item.url, exc)
            return JudgeResult(dropped=[f"llm call failed: {exc}"])

        if getattr(response, "stop_reason", None) == "refusal":
            log.warning("significance step refused for %s", item.url)
            return JudgeResult(dropped=["llm refused the document"])

        payload = _response_text(response)
        try:
            findings = json.loads(payload).get("findings", [])
        except (json.JSONDecodeError, AttributeError) as exc:
            log.warning("significance step returned unparseable output: %s", exc)
            return JudgeResult(dropped=[f"unparseable llm output: {exc}"])
        return _verify(findings, source_text)


def _response_text(response: Any) -> str:
    parsed = getattr(response, "parsed_output", None)
    if parsed is not None:
        return json.dumps(parsed if isinstance(parsed, dict) else parsed.__dict__)
    chunks = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            chunks.append(block.text)
    return "".join(chunks).strip()


def build_judge(enabled: bool = True) -> Judge:
    """Pick the judge: Claude when a key is configured, rules-only otherwise."""
    if not enabled:
        return NullJudge()
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        log.info("no Anthropic credential found; significance step runs on rules only")
        return NullJudge()
    try:
        import anthropic  # noqa: F401
    except ImportError:
        log.info("anthropic SDK not installed; significance step runs on rules only")
        return NullJudge()
    return ClaudeJudge()


def apply(item: Item, source_text: str, judge: Judge) -> JudgeResult:
    """Run the significance step and write the evidence onto the item."""
    result = judge.judge(item, source_text)
    if result.signals:
        item.evidence.signals = sorted(set(item.evidence.signals) | set(result.signals))
    if result.quotes:
        item.evidence.quotes.extend(result.quotes)
    if result.dropped:
        item.meta.setdefault("evidence_dropped", []).extend(result.dropped)
    return result
