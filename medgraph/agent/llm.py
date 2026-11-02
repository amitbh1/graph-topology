"""Pluggable chat-model backend: local Ollama or any OpenAI-compatible endpoint
(e.g. a local OpenCode API server). The model must support tool calling.
Requests are bounded by LLM_TIMEOUT_S and LLM_MAX_TOKENS."""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from ..config import settings


def get_llm(temperature: float = 0.0) -> BaseChatModel:
    if settings.llm_backend == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(model=settings.llm_model, base_url=settings.ollama_base_url,
                          temperature=temperature, num_predict=settings.llm_max_tokens,
                          client_kwargs={"timeout": settings.llm_timeout_s})
    if settings.llm_backend in ("openai", "opencode"):
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=settings.llm_model, base_url=settings.openai_base_url,
                          api_key=settings.openai_api_key, temperature=temperature,
                          max_tokens=settings.llm_max_tokens, timeout=settings.llm_timeout_s,
                          max_retries=2)
    raise ValueError(f"unknown LLM_BACKEND {settings.llm_backend!r} (use ollama or openai)")
