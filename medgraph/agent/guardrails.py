"""Input and output guardrails around the assistant.

Input:  sanitise and bound the question and history; short-circuit crisis
        messages to emergency guidance without calling the model.
Tools:  neutralise prompt-injection phrases in public-source text and wrap
        results as untrusted data.
Output: identifier grounding (grounding.check_identifiers), length cap and a
        standing disclaimer.
"""

from __future__ import annotations

import re

from ..config import settings
from ..security.sanitize import InputError, clean_text

DISCLAIMER = ("_MedGraph reports what its knowledge graph contains. It is not medical advice; "
              "consult a qualified clinician for medical decisions._")

CRISIS_RESPONSE = (
    "It sounds like this may be urgent. MedGraph cannot help in an emergency.\n\n"
    "- If someone may have taken an overdose or is in danger, call your local emergency number now "
    "(e.g. 911 in the US, 112 in the EU, 999 in the UK).\n"
    "- For poisoning questions in the US, call Poison Control at 1-800-222-1222.\n"
    "- If you are thinking about harming yourself, please contact a crisis line — in the US call or "
    "text 988 — or your local emergency services.\n\n"
    "You deserve support from a person who can help right away."
)

_CRISIS = re.compile(
    r"\b(kill (myself|me)|suicid\w*|end (my|his|her|their) life|want to die|self[- ]harm|"
    r"(took|taken|swallowed|ate) (too many|a lot of|an? (whole )?(bottle|pack)|all (my|the|of))|"
    r"overdos(e|ed|ing) (on|right now|now)|not breathing|unconscious|unresponsive)\b",
    re.IGNORECASE,
)

_INJECTION = re.compile(
    r"(ignore|disregard|forget|override) (all |any |the )?(previous|prior|above|earlier|system)?\s*"
    r"(instructions|prompts?|rules|messages)"
    r"|you are now\b|act as (a|an|the)\b|new instructions\s*:|system prompt|developer message"
    r"|</?(system|assistant|user|tool)>|\[/?INST\]",
    re.IGNORECASE,
)


def is_crisis(question: str) -> bool:
    return bool(_CRISIS.search(question))


def clean_question(question: str) -> str:
    return clean_text(question, settings.question_max_chars, field="question")


def clean_history(history: list[dict] | None) -> list[dict]:
    """Only user/assistant turns, bounded in number and size; anything else is rejected."""
    if not history:
        return []
    if not isinstance(history, list):
        raise InputError("history must be a list")
    out = []
    for turn in history[-settings.history_max_turns * 2:]:
        if not isinstance(turn, dict) or turn.get("role") not in ("user", "assistant"):
            raise InputError("history turns must be {role: user|assistant, content: str}")
        out.append({"role": turn["role"],
                    "content": clean_text(turn.get("content"), settings.history_turn_max_chars,
                                          field="history content")})
    return out


def neutralize(text: str) -> str:
    """Defang instruction-like phrases inside untrusted public-source text."""
    return _INJECTION.sub("[removed]", text)


def finalize_answer(text: str) -> str:
    text = (text or "").strip()
    if len(text) > settings.answer_max_chars:
        text = text[: settings.answer_max_chars].rsplit(" ", 1)[0] + " …(truncated)"
    if DISCLAIMER not in text:
        text += "\n\n" + DISCLAIMER
    return text
