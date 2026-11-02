"""LangGraph ReAct agent over the MedGraph tools, wrapped in production guardrails.

Request pipeline:
    sanitise question + history -> crisis short-circuit -> ReAct loop
    (tool budget, recursion limit, overall deadline, LLM/DB timeouts)
    -> identifier grounding check -> length cap + disclaimer -> audit log
"""

from __future__ import annotations

import contextvars
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.prebuilt import create_react_agent

from ..config import settings
from ..db import Neo4jClient
from ..observability import fingerprint, log, metrics
from .grounding import SYSTEM_PROMPT, check_identifiers
from .guardrails import CRISIS_RESPONSE, clean_history, clean_question, finalize_answer, is_crisis
from .llm import get_llm
from .tools import RunState, build_tools, run_state

logger = logging.getLogger("medgraph.agent")

NO_ANSWER = "I could not produce an answer from the graph."
TIMEOUT_ANSWER = ("The request took too long and was stopped. Try a narrower question, "
                  "e.g. one drug or one condition at a time.")


@dataclass
class AgentAnswer:
    answer: str
    tool_calls: list[dict] = field(default_factory=list)
    unverified_ids: list[str] = field(default_factory=list)
    status: str = "ok"            # ok | crisis | timeout | error
    duration_ms: int = 0


class MedGraphAgent:
    def __init__(self, client: Neo4jClient | None = None, llm=None):
        self.llm = llm or get_llm()
        self.graph = create_react_agent(self.llm, build_tools(client, self.llm), prompt=SYSTEM_PROMPT)
        self._pool = ThreadPoolExecutor(max_workers=max(1, settings.agent_max_concurrency),
                                        thread_name_prefix="medgraph-agent")

    def ask(self, question: str, history: list[dict] | None = None, *, principal: str = "cli") -> AgentAnswer:
        start = time.perf_counter()
        question = clean_question(question)  # raises InputError -> 422 at the API
        turns = clean_history(history)

        if is_crisis(question):
            ans = AgentAnswer(answer=CRISIS_RESPONSE, status="crisis")
        else:
            ans = self._run(question, turns)
        ans.duration_ms = round((time.perf_counter() - start) * 1000)

        metrics.inc("medgraph_chat_total", status=ans.status)
        metrics.inc("medgraph_chat_unverified_ids_total", len(ans.unverified_ids))
        log(logger, logging.INFO, "chat", principal=principal, status=ans.status, ms=ans.duration_ms,
            question=question if settings.log_question_text else None,
            question_sha=fingerprint(question), question_chars=len(question),
            tools=[c["tool"] for c in ans.tool_calls], unverified_ids=ans.unverified_ids)
        return ans

    def _run(self, question: str, turns: list[dict]) -> AgentAnswer:
        messages = [(HumanMessage if t["role"] == "user" else AIMessage)(content=t["content"]) for t in turns]
        messages.append(HumanMessage(content=question))

        state = RunState(budget=settings.agent_max_tool_calls)
        ctx = contextvars.copy_context()
        ctx.run(run_state.set, state)
        config = {"recursion_limit": 2 * settings.agent_max_tool_calls + 4}
        future = self._pool.submit(ctx.run, self.graph.invoke, {"messages": messages}, config)
        try:
            result = future.result(timeout=settings.agent_deadline_s)
        except FutureTimeout:
            future.cancel()
            return AgentAnswer(answer=finalize_answer(TIMEOUT_ANSWER), tool_calls=state.calls, status="timeout")
        except Exception as exc:  # recursion limit, model/backend errors
            logger.warning("agent run failed: %s", type(exc).__name__)
            text = ("I reached my tool-call limit before finishing; please ask a narrower question."
                    if "recursion" in type(exc).__name__.lower() else NO_ANSWER)
            return AgentAnswer(answer=finalize_answer(text), tool_calls=state.calls, status="error")

        new = result["messages"][len(messages):]
        final = next((m for m in reversed(new) if isinstance(m, AIMessage) and not m.tool_calls), None)
        text = str(final.content) if final and str(final.content).strip() else NO_ANSWER
        text, unverified = check_identifiers(text, "\n".join(state.evidence))
        return AgentAnswer(answer=finalize_answer(text), tool_calls=state.calls, unverified_ids=unverified)
