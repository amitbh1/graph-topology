"""Runtime configuration, read from environment variables / .env.

`settings.problems()` lists unsafe or invalid values; the API refuses to start in
production (MEDGRAPH_ENV=production) while any exist.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DEFAULT_NEO4J_PASSWORD = "medgraph-local"


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
    env: str = os.getenv("MEDGRAPH_ENV", "development")

    # --- Neo4j -----------------------------------------------------------------
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", DEFAULT_NEO4J_PASSWORD)
    neo4j_database: str = os.getenv("NEO4J_DATABASE", "neo4j")
    # Optional separate least-privilege account for the API (Neo4j Enterprise RBAC).
    neo4j_read_user: str = os.getenv("NEO4J_READ_USER", "")
    neo4j_read_password: str = os.getenv("NEO4J_READ_PASSWORD", "")
    query_timeout_s: float = _float("NEO4J_QUERY_TIMEOUT_S", 15.0)
    neo4j_pool_size: int = _int("NEO4J_POOL_SIZE", 50)

    # --- Embeddings ------------------------------------------------------------
    embedding_backend: str = os.getenv("EMBEDDING_BACKEND", "ollama")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
    embedding_dim: int = _int("EMBEDDING_DIM", 768)
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

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
    # Comma-separated `name:scope1|scope2:sha256hex` entries (see `python -m medgraph apikey`).
    api_keys: tuple[str, ...] = _list("MEDGRAPH_API_KEYS")
    auth_disabled: bool = _bool("MEDGRAPH_AUTH_DISABLED", False)
    cors_origins: tuple[str, ...] = _list("CORS_ORIGINS")
    trusted_proxies: tuple[str, ...] = _list("TRUSTED_PROXIES")
    max_body_bytes: int = _int("MAX_BODY_BYTES", 64 * 1024)
    rate_limit_read_per_min: int = _int("RATE_LIMIT_READ_PER_MIN", 120)
    rate_limit_chat_per_min: int = _int("RATE_LIMIT_CHAT_PER_MIN", 10)
    rate_limit_anon_per_min: int = _int("RATE_LIMIT_ANON_PER_MIN", 30)
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    # Questions can contain health information: audit logs store a hash unless enabled.
    log_question_text: bool = _bool("LOG_QUESTION_TEXT", False)

    # --- ETL sizes -------------------------------------------------------------
    label_limit: int = _int("ETL_LABEL_LIMIT", 250)
    faers_per_drug: int = _int("ETL_FAERS_PER_DRUG", 10)
    trials_per_condition: int = _int("ETL_TRIALS_PER_CONDITION", 20)
    trial_conditions: int = _int("ETL_TRIAL_CONDITIONS", 40)
    atc_csv_url: str = os.getenv(
        "ATC_CSV_URL",
        "https://raw.githubusercontent.com/fabkury/atcd/master/WHO%20ATC-DDD%202026-04-25.csv",
    )
    mesh_xml_url: str = os.getenv(
        "MESH_XML_URL",
        "https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/desc2026.gz",
    )

    ontology_path: Path = ROOT / "ontology" / "medical_graph.ttl"
    data_dir: Path = ROOT / "data"
    cache_dir: Path = field(default_factory=lambda: ROOT / "data" / "cache")

    @property
    def is_production(self) -> bool:
        return self.env.lower() in ("production", "prod")

    def problems(self) -> list[str]:
        """Invalid values always; unsafe values only matter in production."""
        out = []
        if self.llm_backend not in ("ollama", "openai", "opencode"):
            out.append(f"LLM_BACKEND must be ollama or openai, not {self.llm_backend!r}")
        if self.embedding_backend not in ("ollama", "hash"):
            out.append(f"EMBEDDING_BACKEND must be ollama or hash, not {self.embedding_backend!r}")
        for name in ("query_timeout_s", "llm_timeout_s", "agent_deadline_s"):
            if getattr(self, name) <= 0:
                out.append(f"{name} must be positive")
        if not 1 <= self.agent_max_tool_calls <= 25:
            out.append("AGENT_MAX_TOOL_CALLS must be between 1 and 25")
        if self.is_production:
            if self.auth_disabled:
                out.append("MEDGRAPH_AUTH_DISABLED=true is not allowed in production")
            if not self.api_keys:
                out.append("MEDGRAPH_API_KEYS must be set in production")
            if self.neo4j_password == DEFAULT_NEO4J_PASSWORD:
                out.append("NEO4J_PASSWORD still has the development default")
            if "*" in self.cors_origins:
                out.append("CORS_ORIGINS=* is not allowed in production")
            if self.embedding_backend == "hash":
                out.append("EMBEDDING_BACKEND=hash is a lexical test fallback, not for production")
        return out


settings = Settings()
