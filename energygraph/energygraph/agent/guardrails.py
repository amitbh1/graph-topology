"""Input and output guardrails around the assistant.

Input:  sanitise / bound question and history; deterministic short-circuits for
        (a) physical emergencies (gas leak, fire, explosion) and
        (b) trading / investment advice requests.
Tools:  neutralise prompt-injection phrases in untrusted text.
Output: grounding (grounding.check_answer), forecast-language flag, length cap,
        standing disclaimer.
"""

from __future__ import annotations

import re

from ..config import settings
from ..security.sanitize import InputError, clean_text

DISCLAIMER = ("_EnergyGraph reports what its knowledge graph and licensed sources contain, with their as-of dates. "
              "It is not investment, trading or operational advice._")

EMERGENCY_RESPONSE = (
    "This sounds like a possible emergency. EnergyGraph cannot help with an active incident.\n\n"
    "- Leave the area and keep others away. Do not use switches, phones or open flames near a suspected gas leak.\n"
    "- Call your local emergency number now (e.g. 911 in the US, 112 in the EU, 999 in the UK).\n"
    "- If you are at a facility, follow its emergency procedures and notify the operator's control room.\n"
    "- In the US, pipeline emergencies can also be reported to the National Response Center: 1-800-424-8802."
)

ADVICE_RESPONSE = (
    "EnergyGraph does not give trading, investment or hedging recommendations, and it does not forecast prices. "
    "I can show published price history, assets, ownership, outages and projects with their sources, which you "
    "can use in your own analysis. For example: \"Show Henry Hub prices for the last 90 days\" or "
    "\"Which LNG projects reached FID in the US?\""
)

_EMERGENCY = re.compile(
    r"\b(i (can )?smell gas|gas leak (in|at|near) (my|our|the) (house|home|building|office|street)|"
    r"(there is|there's) (a )?(fire|explosion)|(pipeline|tank|plant) (is )?(on fire|exploding|leaking) (now|right now)|"
    r"someone (is )?(injured|unconscious|trapped))\b", re.IGNORECASE)

_ADVICE = re.compile(
    r"\b(should i (buy|sell|short|go long|invest|hedge)|(buy|sell) (signal|recommendation)|"
    r"is it a good (time|idea) to (buy|sell|invest)|which (stock|share)s? should i|"
    r"(give|tell) me (a )?(trade|trading) (idea|tip|recommendation)s?|what will (the )?price[s]? (be|do)|"
    r"(predict|forecast) (the )?(price|prices))\b", re.IGNORECASE)

_INJECTION = re.compile(
    r"(ignore|disregard|forget|override) (all |any |the )?(previous|prior|above|earlier|system)?\s*"
    r"(instructions|prompts?|rules|messages)|you are now\b|act as (a|an|the)\b|new instructions\s*:"
    r"|system prompt|developer message|</?(system|assistant|user|tool)>|\[/?INST\]",
    re.IGNORECASE)

_FORECAST = re.compile(r"\b(will (rise|fall|increase|decrease|reach|hit)|is expected to (rise|fall|reach)|"
                       r"forecast to|likely to (rise|fall))\b", re.IGNORECASE)


def is_emergency(question: str) -> bool:
    return bool(_EMERGENCY.search(question))


def is_advice_request(question: str) -> bool:
    return bool(_ADVICE.search(question))


def clean_question(question: str) -> str:
    return clean_text(question, settings.question_max_chars, field="question")


def clean_history(history) -> list[dict]:
    if not history:
        return []
    if not isinstance(history, list):
        raise InputError("history must be a list")
    out = []
    for turn in history[-settings.history_max_turns * 2:]:
        if not isinstance(turn, dict) or turn.get("role") not in ("user", "assistant"):
            raise InputError("history turns must be {role: user|assistant, content: str}")
        out.append({"role": turn["role"], "content": clean_text(turn.get("content"), settings.history_turn_max_chars,
                                                                field="history content")})
    return out


def neutralize(text: str) -> str:
    return _INJECTION.sub("[removed]", text)


def finalize_answer(text: str) -> str:
    text = (text or "").strip()
    if _FORECAST.search(text):
        text += ("\n\n_Note: EnergyGraph does not forecast. Any forward-looking wording above is not supported by "
                 "the graph; rely only on the cited published data._")
    if len(text) > settings.answer_max_chars:
        text = text[: settings.answer_max_chars].rsplit(" ", 1)[0] + " …(truncated)"
    if DISCLAIMER not in text:
        text += "\n\n" + DISCLAIMER
    return text
