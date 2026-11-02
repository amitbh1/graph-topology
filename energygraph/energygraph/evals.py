"""Behavioural evaluation of the assistant (evals/cases.json).

    python -m energygraph eval        # needs a loaded graph and the configured LLM

Checks per case: expected tools used, required / forbidden phrases, no
unverified identifiers or numbers, expected status, and — for entitlement
cases — that restricted content never appears for a public-only principal.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import ROOT
from .security.auth import Principal

CASES = ROOT / "evals" / "cases.json"
REQUIRED = {"id", "question", "expect_tools", "must_contain", "must_not_contain"}


def load_cases(path: Path = CASES) -> list[dict]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    for c in cases:
        missing = REQUIRED - c.keys()
        if missing:
            raise ValueError(f"eval case {c.get('id')} is missing {sorted(missing)}")
    return cases


def principal_for(case: dict) -> Principal:
    ents = frozenset(case.get("entitlements", ["public"]))
    return Principal(f"eval:{case['id']}", frozenset({"read", "chat"}), ents | {"public"}, kind="eval")


def score(case: dict, ans) -> list[str]:
    failures = []
    used = {c["tool"] for c in ans.tool_calls}
    failures += [f"expected tool {t} (used: {sorted(used) or 'none'})" for t in case["expect_tools"] if t not in used]
    text = ans.answer.lower()
    failures += [f"missing '{p}'" for p in case["must_contain"] if p.lower() not in text]
    failures += [f"forbidden '{p}'" for p in case["must_not_contain"] if p.lower() in text]
    if ans.unverified_ids or ans.unverified_numbers:
        failures.append(f"ungrounded: ids={ans.unverified_ids} numbers={ans.unverified_numbers}")
    if case.get("expect_status") and ans.status != case["expect_status"]:
        failures.append(f"status {ans.status} != {case['expect_status']}")
    return failures


def run_evals(agent) -> int:
    cases = load_cases()
    passed = 0
    for case in cases:
        ans = agent.ask(case["question"], principal=principal_for(case))
        failures = score(case, ans)
        passed += not failures
        print(f"[{'PASS' if not failures else 'FAIL'}] {case['id']:24s} {ans.duration_ms:6d} ms "
              f"tools={[c['tool'] for c in ans.tool_calls]}")
        for f in failures:
            print(f"         - {f}")
    print(f"\n{passed}/{len(cases)} passed")
    return 0 if passed == len(cases) else 1
