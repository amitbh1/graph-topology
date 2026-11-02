# MedGraph — practices applied in the code

This lists every engineering, security and agentic practice the code applies, and where.
Each item says **what** is done, **why**, and **where** to find it.

---

## 1. Agentic AI practices

| # | Practice | Why | Where |
|---|---|---|---|
| A1 | **Deterministic tools for multi-step questions.** For example, `disease_overview` walks concept → narrower concepts → drugs → reactions → trials → ICD-10 in a single call. | Small models get multi-step planning wrong. A deterministic tool gives the same correct answer every time. | [queries.py](medgraph/queries.py) |
| A2 | **Small, typed tool surface.** Nine tools, each with a pydantic `args_schema` (types, `maxLength`, enums such as trial status). | The model can only call tools with valid arguments, and schema errors come back as readable feedback. | [agent/tools.py](medgraph/agent/tools.py) |
| A3 | **Arguments sanitised again inside each tool.** | The schema checks the shape; sanitisation strips input like `"drug: Glucotrol"`, quotes and control characters. | `clean_entity` in [security/sanitize.py](medgraph/security/sanitize.py) |
| A4 | **Entity resolution before querying** (exact → alias → fuzzy). | Brand names, salts and misspellings still reach the right node. The model doesn't have to know canonical names. | `resolve_drug`, `resolve_concept` |
| A5 | **Grounding rules in the system prompt.** Report only tool results; say "not in the graph" when the graph has no answer. | Fluent answers that can't be traced to data are unsafe in medicine. | [agent/grounding.py](medgraph/agent/grounding.py) |
| A6 | **Post-answer identifier verification.** NCT, ATC, report and ICD-10 ids in the answer must appear in a tool result; any others are redacted and flagged. | Small models were seen inventing NCT ids. This turns a hallucinated id into a visible, logged event. | `check_identifiers` |
| A7 | **"Not found" is a first-class result** (`found: false` plus a message). | The model gets an explicit signal to say it doesn't know. | every query function |
| A8 | **Tool results wrapped as untrusted data**: `{"tool", "untrusted_data"}`. The system prompt forbids following instructions found inside them. | Label and trial text comes from public sources. Treating it as data limits prompt injection. | `_envelope` in tools.py |
| A9 | **Injection phrases neutralised** in tool output ("ignore previous instructions", role tags, `[INST]`, …). | Defence in depth, on top of A8. | `neutralize` in [agent/guardrails.py](medgraph/agent/guardrails.py) |
| A10 | **Per-request tool budget** (`AGENT_MAX_TOOL_CALLS`). Once spent, tools return "answer now with what you have". | Stops tool loops and runaway cost. The model gets a graceful signal instead of a crash. | `RunState`, `guarded` |
| A11 | **Recursion limit** = 2 × budget + 4 LangGraph steps. | A hard stop beneath the budget. | [agent/agent.py](medgraph/agent/agent.py) |
| A12 | **Overall deadline** (`AGENT_DEADLINE_S`), plus LLM timeout and token cap (`LLM_TIMEOUT_S`, `LLM_MAX_TOKENS`). | Bounds latency and cost for every request. | agent.py, [agent/llm.py](medgraph/agent/llm.py) |
| A13 | **Tool errors isolated.** Exceptions become `{"found": false, "error": ...}` without internal details. | One failing query must not crash the run or leak the stack to the model or user. | `guarded` |
| A14 | **Tool output size cap** (`TOOL_OUTPUT_MAX_CHARS`). | Protects the context window and cost. | `_envelope` |
| A15 | **History validation.** Only `user`/`assistant` roles, bounded turns and size. | Prevents injection of `system` turns or context stuffing through the history field. | `clean_history` |
| A16 | **Crisis short-circuit.** Overdose and self-harm messages get emergency guidance without calling the model. | Safety-critical replies must be deterministic and immediate. | `is_crisis`, `CRISIS_RESPONSE` |
| A17 | **Standing disclaimer and answer length cap** on every answer. | Medical-advice boundary, applied consistently. | `finalize_answer` |
| A18 | **LLM-generated Cypher is guarded** (`graph_qa`): a single statement, no write clauses, no procedure calls except vector/fulltext search, only ontology-declared labels and relationship types, forced `LIMIT`, row cap. It runs in a READ transaction with a server timeout and can be disabled (`ENABLE_GRAPH_QA`). | Text-to-query is the riskiest tool. Several independent layers protect it. | `guard_cypher`, `graph_qa` |
| A19 | **Least privilege for the agent.** It only gets a read-only DB client. | Even a fully hijacked model cannot change data. | `get_read_client` in [db.py](medgraph/db.py) |
| A20 | **Pluggable, local-first model backend** (Ollama or any OpenAI-compatible server). Tool-calling models required. | Data stays local, models can be swapped, and models without tool-calling support (which broke arguments in testing) are avoided. | llm.py |
| A21 | **Traceability.** Each answer returns the tool calls (args, outcome, ms), unverified ids, status and request id. | Users and reviewers can see where every fact came from. | `AgentAnswer`, `/api/chat` |
| A22 | **Behavioural evals.** Golden cases check tool choice, required and forbidden phrases, invented ids, injection resistance and crisis handling. | Regression-test the agent whenever the model, prompt or tools change. | [evals/cases.json](evals/cases.json), [medgraph/evals.py](medgraph/evals.py), `python -m medgraph eval` |
| A23 | **Offline agent tests with a scripted fake model.** | The LangGraph wiring, budget, grounding and guardrails are tested in CI without an LLM. | [tests/test_agent.py](tests/test_agent.py) |
| A24 | **Domain caveats encoded in tool output.** FAERS = association, not causation; similarity is exploratory. | The caveat travels with the data, so the model repeats it. | `SAFETY_NOTE`, `SIMILARITY_NOTE` |

## 2. Authentication and authorisation

| # | Practice | Where |
|---|---|---|
| S1 | API keys required on every `/api/*` route and `/metrics`, via `X-API-Key` or `Authorization: Bearer`. | [api/security.py](medgraph/api/security.py) `require()` |
| S2 | **Only SHA-256 hashes of keys are stored.** The key is shown once at creation (`python -m medgraph apikey`). Keys carry a `mgk_` prefix so secret scanners can spot them. | [security/auth.py](medgraph/security/auth.py) |
| S3 | **Constant-time comparison** (`hmac.compare_digest`) against every configured key. | `KeyStore.authenticate` |
| S4 | **Scopes / RBAC:** `read` (graph), `chat` (assistant), `admin` (stats, metrics). 403 is returned when the scope is missing. | `Principal.can`, route dependencies |
| S5 | Key config is **validated at startup** (format, known scopes, 64-hex digest). A bad config fails fast. | `parse_key_entries`, API lifespan |
| S6 | Failed auth attempts are rate limited per IP, which slows key guessing. | `require()` |
| S7 | "Auth disabled" exists **for development only** and is refused in production. | `Settings.problems()` |

## 3. Limits and abuse protection

| # | Practice | Where |
|---|---|---|
| L1 | Token-bucket rate limits per key + IP. Chat (LLM) has its own, stricter limit. Responses are 429 with `Retry-After`. | [security/ratelimit.py](medgraph/security/ratelimit.py) |
| L2 | **Concurrency gate** for the assistant. Extra requests get 503 with `Retry-After` instead of queueing without bound. | `ConcurrencyGate` |
| L3 | **Request body limit** (`MAX_BODY_BYTES`), enforced on `Content-Length` *and* on the streamed byte count (catches chunked bodies). | `RequestContextMiddleware` |
| L4 | Bounded query parameters: name length ≤ 120, `limit` 1–50, `k` 1–20, `max_depth` 0–20. | [api/main.py](medgraph/api/main.py) |
| L5 | Bounded chat payload: question length, history turns and turn size. Unknown fields are rejected (`extra="forbid"`). | `ChatRequest`, `Turn` |
| L6 | **Server-side query timeout** on every Neo4j query (`NEO4J_QUERY_TIMEOUT_S`), plus a bounded connection pool and acquisition timeout. | [db.py](medgraph/db.py) |
| L7 | Rate-limiter memory is bounded (`max_keys` with eviction). | `RateLimiter._evict` |
| L8 | uvicorn keep-alive timeout is bounded and the server header is hidden. | `__main__.py serve` |

## 4. Input validation and sanitisation

| # | Practice | Where |
|---|---|---|
| V1 | Unicode **NFKC normalisation**; zero-width and bidi-override characters (used to hide text) removed; control characters stripped; whitespace collapsed. | `clean_text` |
| V2 | **Allow-list** of characters for entity arguments (drug, disease and code names). | `clean_entity` |
| V3 | **Enum allow-list** for trial status; ATC codes are matched against their exact pattern. | `clean_status`, `ATC_CODE` |
| V4 | **Parameterised Cypher everywhere.** User values never go into query text. The only interpolated values are ints clamped by code or ontology-declared labels. | [queries.py](medgraph/queries.py) |
| V5 | Lucene **phrase escaping** for full-text search, and fuzzy queries built only from `[A-Za-z0-9]` tokens. | `_lucene_phrase`, `_lucene_fuzzy` |
| V6 | openFDA search values quoted with operators removed, so a value cannot break out of the search expression. | `lucene_value` in [etl/common.py](medgraph/etl/common.py) |
| V7 | Validation errors say **which field and why**, but never echo the submitted value. | `validation_error` handler |
| V8 | Output escaping in the dashboard (`esc()` for `& < > " '`); no `eval`; data rendered as text. | [web/app.js](medgraph/web/app.js) |

## 5. HTTP and deployment hardening

| # | Practice | Where |
|---|---|---|
| H1 | Security headers: CSP (`script-src 'self'`, `frame-ancestors 'none'`, `base-uri 'none'`), `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, COOP. HSTS is added in production. | `SECURITY_HEADERS` |
| H2 | Dashboard JavaScript moved to a static file, so the CSP can forbid inline scripts. | [web/app.js](medgraph/web/app.js) |
| H3 | CORS off by default; an explicit origin list only; `*` refused in production. | `Settings.problems()`, main.py |
| H4 | `X-Forwarded-For` honoured **only from configured trusted proxies** (client IPs cannot be spoofed to dodge rate limits). | `client_ip` |
| H5 | OpenAPI and docs disabled in production. | main.py |
| H6 | Uniform error envelope `{error: {status, message, request_id}}`. 500s are generic, with details only in the server log. | exception handlers |
| H7 | **Fail-fast production config check.** The API refuses to start in production with auth disabled, no keys, the default DB password, a CORS wildcard or the hash embedder. `python -m medgraph check-config` shows the same checks. | [config.py](medgraph/config.py) |
| H8 | Container: slim base, **non-root user**, read-only root filesystem, `no-new-privileges`, all capabilities dropped, CPU and memory limits, healthcheck. | [Dockerfile](Dockerfile), [docker-compose.yml](docker-compose.yml) |
| H9 | Ports bound to `127.0.0.1` only; TLS is expected at a reverse proxy. | docker-compose.yml |
| H10 | Secrets only via environment / `.env`, which is ignored by both git and docker. | `.gitignore`, `.dockerignore` |

## 6. Data access

| # | Practice | Where |
|---|---|---|
| D1 | API and assistant use a **read-only client**: local writes raise, and every query runs in a READ transaction. **The server rejects writes** (verified: `Neo.ClientError.Statement.AccessMode`). | [db.py](medgraph/db.py) |
| D2 | Optional separate least-privilege DB account (`NEO4J_READ_USER`, Neo4j Enterprise RBAC). | config.py |
| D3 | **Ontology as schema contract.** Constraints and vector indexes are generated from it. Tests fail on any undeclared label or relationship in code or database, and on domain/range violations in the data. | [ontology.py](medgraph/ontology.py), [validate.py](medgraph/validate.py) |
| D4 | Idempotent ETL (`MERGE`), batched writes, atomic best-effort HTTP cache, retries with exponential back-off on 429/5xx. | [etl/](medgraph/etl/) |
| D5 | Data-quality safeguards: RxNorm ingredient canonicalisation, MedDRA British→American spelling, negated-sentence filtering for indications, FAERS suspect-role check. | etl modules |
| D6 | Sample data is clearly marked synthetic (`SEED-AE-*`, `NCT-SEED-*`, `source` property). | [etl/seed.py](medgraph/etl/seed.py) |

## 7. Observability and privacy

| # | Practice | Where |
|---|---|---|
| O1 | Structured **JSON logs** with a per-request id (an incoming `X-Request-ID` is accepted only if it matches a safe pattern; it is echoed in the response). | [observability.py](medgraph/observability.py) |
| O2 | **Audit log per chat**: principal, status, latency, tools used, unverified ids. | `MedGraphAgent.ask` |
| O3 | **Privacy by default:** questions are logged as a SHA-256 fingerprint and length, not as text (`LOG_QUESTION_TEXT=false`). | `fingerprint` |
| O4 | Prometheus metrics (`/metrics`, admin only): requests, latency sums, tool outcomes, rate-limit hits, auth failures, unverified ids. Labels use route templates, which keeps cardinality bounded. | `Metrics` |
| O5 | Liveness `/healthz` and readiness `/readyz` (checks Neo4j). | main.py |

## 8. Testing

| # | Practice | Where |
|---|---|---|
| T1 | Unit tests for parsers, sanitisers, auth, rate limits and guardrails. | `tests/test_etl.py`, `tests/test_security.py`, `tests/test_grounding.py` |
| T2 | API tests for 401/403/413/422/429, header checks, and no input echo. | `tests/test_security.py` |
| T3 | Ontology contract tests over all source code. | `tests/test_ontology.py` |
| T4 | Integration tests against a loaded Neo4j, skipped automatically when none is available. | `tests/test_integration.py`, `tests/test_agent.py` |

---

## Known limits (deliberate, documented)

* Rate limits and the concurrency gate are **per process**. With several replicas, use a shared store (e.g. Redis) or enforce limits at the gateway.
* Neo4j Community has no RBAC. Read-only access is enforced per transaction. Use Enterprise + `NEO4J_READ_USER` for account-level least privilege.
* API keys are static secrets. For human users behind SSO, put an OIDC proxy in front, or replace `KeyStore` with JWT validation. The `require(scope)` interface stays the same.
* Keyword guardrails (crisis, injection) are a first layer, not a classifier. Evals (A22) measure how well they work in practice.
* An agent run that hits the deadline is reported to the caller immediately. Its worker thread finishes on its own, bounded by the LLM and DB timeouts.
