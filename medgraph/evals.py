"""Behavioural evaluation of the assistant against golden cases (evals/cases.json).

    python -m medgraph eval            # needs a loaded graph and the configured LLM

Each case checks: expected tools were used, required phrases are present,
forbidden phrases are absent, no unverified identifiers survived, and the
expected status (e.g. crisis). Run it whenever the model, prompt or tools change.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import ROOT

CASES = ROOT / "evals" / "cases.json"


def load_cases(path: Path = CASES) -> list[dict]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    for c in cases:
        missing = {"id", "question", "expect_tools", "must_contain", "must_not_contain"} - c.keys()
        if missing:
            raise ValueError(f"eval case {c.get('id')} is missing {sorted(missing)}")
    return cases


def score(case: dict, answer) -> list[str]:
    failures = []
    used = {c["tool"] for c in answer.tool_calls}
    for tool in case["expect_tools"]:
        if tool not in used:
            failures.append(f"expected tool {tool} (used: {sorted(used) or 'none'})")
    text = answer.answer.lower()
    failures += [f"missing '{p}'" for p in case["must_contain"] if p.lower() not in text]
    failures += [f"forbidden '{p}'" for p in case["must_not_contain"] if p.lower() in text]
    if answer.unverified_ids:
        failures.append(f"model invented ids {answer.unverified_ids}")
    if case.get("expect_status") and answer.status != case["expect_status"]:
        failures.append(f"status {answer.status} != {case['expect_status']}")
    return failures


def run_evals(agent) -> int:
    cases = load_cases()
    passed = 0
    for case in cases:
        ans = agent.ask(case["question"], principal="eval")
        failures = score(case, ans)
        passed += not failures
        mark = "PASS" if not failures else "FAIL"
        print(f"[{mark}] {case['id']:20s} {ans.duration_ms:6d} ms  tools={[c['tool'] for c in ans.tool_calls]}")
        for f in failures:
            print(f"         - {f}")
    print(f"\n{passed}/{len(cases)} passed")
    return 0 if passed == len(cases) else 1
