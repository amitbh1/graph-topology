"""Runtime configuration from environment variables / .env.

`settings.problems()` lists invalid or unsafe values. In production
(ENERGYGRAPH_ENV=production) the API refuses to start while any exist.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DEFAULT_NEO4J_PASSWORD = "energygraph-local"


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _list(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(x.strip() for x in os.getenv(name, default).split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    env: str = os.getenv("ENERGYGRAPH_ENV", "development")

    # --- Neo4j -----------------------------------------------------------------
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7688")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", DEFAULT_NEO4J_PASSWORD)
    neo4j_database: str = os.getenv("NEO4J_DATABASE", "neo4j")
    neo4j_read_user: str = os.getenv("NEO4J_READ_USER", "")
    neo4j_read_password: str = os.getenv("NEO4J_READ_PASSWORD", "")
    query_timeout_s: float = _float("NEO4J_QUERY_TIMEOUT_S", 15.0)
    neo4j_pool_size: int = _int("NEO4J_POOL_SIZE", 50)

    # --- Time series -----------------------------------------------------------
    timeseries_url: str = os.getenv("TIMESERIES_URL", "sqlite:///data/timeseries.sqlite3")

    # --- Embeddings ------------------------------------------------------------
    embedding_backend: str = os.getenv("EMBEDDING_BACKEND", "ollama")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
    embedding_dim: int = _int("EMBEDDING_DIM", 768)
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11435")

    # --- Language model --------------------------------------------------------
    llm_backend: str = os.getenv("LLM_BACKEND", "ollama")
    llm_model: str = os.getenv("LLM_MODEL", "qwen2.5:7b")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "http://localhost:4096/v1")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "not-needed-for-local")
    llm_timeout_s: float = _float("LLM_TIMEOUT_S", 60.0)
    llm_max_tokens: int = _int("LLM_MAX_TOKENS", 1024)

    # --- Agent limits ----------------------------------------------------------
    agent_deadline_s: float = _float("AGENT_DEADLINE_S", 90.0)
    agent_max_tool_calls: int = _int("AGENT_MAX_TOOL_CALLS", 8)
    agent_max_concurrency: int = _int("AGENT_MAX_CONCURRENCY", 4)
    tool_output_max_chars: int = _int("TOOL_OUTPUT_MAX_CHARS", 6000)
    answer_max_chars: int = _int("ANSWER_MAX_CHARS", 6000)
    question_max_chars: int = _int("QUESTION_MAX_CHARS", 1000)
    history_max_turns: int = _int("HISTORY_MAX_TURNS", 10)
    history_turn_max_chars: int = _int("HISTORY_TURN_MAX_CHARS", 4000)
    enable_graph_qa: bool = _bool("ENABLE_GRAPH_QA", True)
    graph_qa_max_rows: int = _int("GRAPH_QA_MAX_ROWS", 50)

    # --- API security ----------------------------------------------------------
    # API keys: name:scope1|scope2:entitlement1|entitlement2:sha256hex (see `energygraph apikey`)
    api_keys: tuple[str, ...] = _list("ENERGYGRAPH_API_KEYS")
    auth_disabled: bool = _bool("ENERGYGRAPH_AUTH_DISABLED", False)
    dev_entitlements: tuple[str, ...] = _list("DEV_ENTITLEMENTS", "public")
    # OIDC / JWT bearer tokens (optional; enables SSO users)
    oidc_issuer: str = os.getenv("OIDC_ISSUER", "")
    oidc_audience: str = os.getenv("OIDC_AUDIENCE", "")
    oidc_jwks_url: str = os.getenv("OIDC_JWKS_URL", "")
    oidc_scope_claim: str = os.getenv("OIDC_SCOPE_CLAIM", "scope")
    oidc_entitlement_claim: str = os.getenv("OIDC_ENTITLEMENT_CLAIM", "entitlements")
    cors_origins: tuple[str, ...] = _list("CORS_ORIGINS")
    trusted_proxies: tuple[str, ...] = _list("TRUSTED_PROXIES")
    max_body_bytes: int = _int("MAX_BODY_BYTES", 64 * 1024)
    rate_limit_read_per_min: int = _int("RATE_LIMIT_READ_PER_MIN", 120)
    rate_limit_chat_per_min: int = _int("RATE_LIMIT_CHAT_PER_MIN", 10)
    rate_limit_anon_per_min: int = _int("RATE_LIMIT_ANON_PER_MIN", 30)
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_question_text: bool = _bool("LOG_QUESTION_TEXT", False)

    # --- Public sources --------------------------------------------------------
    eia_api_key: str = os.getenv("EIA_API_KEY", "")
    gppd_url: str = os.getenv(
        "GPPD_URL", "https://wri-dataportal-prod.s3.amazonaws.com/manual/global_power_plant_database_v_1_3.zip")
    gppd_min_mw: float = _float("GPPD_MIN_MW", 50.0)
    oeo_url: str = os.getenv(
        "OEO_URL", "https://github.com/OpenEnergyPlatform/ontology/releases/download/v2.14.0/oeo-full.owl")
    gleif_max_companies: int = _int("GLEIF_MAX_COMPANIES", 300)

    # --- Organization sources (masked; integrated later) ----------------------
    # ORG_SOURCE_MODE: disabled | file | http
    org_source_mode: str = os.getenv("ORG_SOURCE_MODE", "disabled")
    org_source_dir: str = os.getenv("ORG_SOURCE_DIR", "")
    org_source_base_url: str = os.getenv("ORG_SOURCE_BASE_URL", "")
    org_source_token: str = os.getenv("ORG_SOURCE_TOKEN", "")
    org_source_datasets: tuple[str, ...] = _list(
        "ORG_SOURCE_DATASETS",
        "companies,power-assets,refinery-assets,lng-assets,pipelines,outages,projects,price-series,prices,news",
    )
    org_source_timeout_s: float = _float("ORG_SOURCE_TIMEOUT_S", 30.0)

    ontology_path: Path = ROOT / "ontology" / "energy_graph.ttl"
    data_dir: Path = ROOT / "data"
    cache_dir: Path = field(default_factory=lambda: ROOT / "data" / "cache")

    @property
    def is_production(self) -> bool:
        return self.env.lower() in ("production", "prod")

    def problems(self) -> list[str]:
        out = []
        if self.llm_backend not in ("ollama", "openai"):
            out.append(f"LLM_BACKEND must be ollama or openai, not {self.llm_backend!r}")
        if self.embedding_backend not in ("ollama", "hash"):
            out.append(f"EMBEDDING_BACKEND must be ollama or hash, not {self.embedding_backend!r}")
        if self.org_source_mode not in ("disabled", "file", "http"):
            out.append("ORG_SOURCE_MODE must be disabled, file or http")
        if self.org_source_mode == "http" and not self.org_source_base_url.startswith("https://"):
            out.append("ORG_SOURCE_BASE_URL must be an https:// URL")
        if self.org_source_mode == "file" and not self.org_source_dir:
            out.append("ORG_SOURCE_DIR is required when ORG_SOURCE_MODE=file")
        for name in ("query_timeout_s", "llm_timeout_s", "agent_deadline_s"):
            if getattr(self, name) <= 0:
                out.append(f"{name} must be positive")
        if not 1 <= self.agent_max_tool_calls <= 25:
            out.append("AGENT_MAX_TOOL_CALLS must be between 1 and 25")
        if bool(self.oidc_issuer) != bool(self.oidc_jwks_url) or (self.oidc_issuer and not self.oidc_audience):
            out.append("OIDC_ISSUER, OIDC_AUDIENCE and OIDC_JWKS_URL must be set together")
        if self.is_production:
            if self.auth_disabled:
                out.append("ENERGYGRAPH_AUTH_DISABLED=true is not allowed in production")
            if not self.api_keys and not self.oidc_issuer:
                out.append("configure ENERGYGRAPH_API_KEYS and/or OIDC in production")
            if self.neo4j_password == DEFAULT_NEO4J_PASSWORD:
                out.append("NEO4J_PASSWORD still has the development default")
            if "*" in self.cors_origins:
                out.append("CORS_ORIGINS=* is not allowed in production")
            if self.embedding_backend == "hash":
                out.append("EMBEDDING_BACKEND=hash is a lexical test fallback, not for production")
            if self.oidc_jwks_url and not self.oidc_jwks_url.startswith("https://"):
                out.append("OIDC_JWKS_URL must be https in production")
        return out


settings = Settings()
