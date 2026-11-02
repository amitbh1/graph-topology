# EnergyGraph — practices applied in the code

Each practice says what is done, why, and where to find it.

## 1. Agentic AI

| # | Practice | Why | Where |
|---|---|---|---|
| A1 | **Deterministic tools** for multi-step questions (`asset_overview`, `ownership_chain`, `supply_path`, `project_pipeline`). | Reliable answers, even with small models. | [queries.py](energygraph/queries.py) |
| A2 | **14 typed tools** with strict pydantic schemas: lengths, enums, ISO-date patterns, numeric bounds. | Invalid arguments are rejected before any query runs, and the model gets a readable error. | [agent/tools.py](energygraph/agent/tools.py) |
| A3 | **Arguments sanitised again inside each tool.** | Defence in depth beyond the schema. | `clean_*` in [security/sanitize.py](energygraph/security/sanitize.py) |
| A4 | **Entity resolution** (id → exact name → alias → fuzzy), returning alternatives. | Names like "Sabine Pass" are ambiguous, so the alternatives are shown. | `resolve_asset`, `resolve_company`, `resolve_concept` |
| A5 | **Caller identity travels in the request context** (`RunState.principal`). Tools called without one see **public data only**. | Entitlements reach every tool through LangGraph's threads. The secure default protects any path that skips the request context. | `run_state`, `current_principal` |
| A6 | **Grounding rules** in the system prompt: tool facts only, quote ids and numbers, state as-of dates and sources, no arithmetic. | Answers must be traceable to data. | [agent/grounding.py](energygraph/agent/grounding.py) |
| A7 | **Identifier and NUMBER verification.** Ids and material numbers in the answer must appear in tool output (rounding tolerance); others are replaced and reported. | A wrong capacity or price is worse than none. | `check_answer` |
| A8 | **No LLM arithmetic.** A deterministic `convert_units` tool reports its factors and assumptions. | Conversions such as crude t↔bbl depend on the grade. | [units.py](energygraph/units.py) |
| A9 | **Untrusted-data envelope + injection neutralisation** on every tool result. | News and filings are attacker-reachable text. Tested with an injection planted in sample licensed news. | `_envelope`, `neutralize` |
| A10 | **Tool budget, recursion limit, overall deadline, LLM timeout and token cap.** | Bounds cost and latency. When the budget runs out, the model is told to answer with what it has. | `RunState`, [agent/agent.py](energygraph/agent/agent.py), [agent/llm.py](energygraph/agent/llm.py) |
| A11 | **Tool error isolation.** Exceptions become structured errors with no internal details. | No crashes, and no leaked connection strings. | `guarded` |
| A12 | **Deterministic short-circuits** for physical emergencies (gas leak, fire) and trading-advice requests. | Safety and compliance answers must not depend on model quality. | [agent/guardrails.py](energygraph/agent/guardrails.py) |
| A13 | **Domain caveats travel with the data:** incidents are reports, similarity is exploratory, prices are published values only. | The model repeats caveats that sit in tool output. | `INCIDENT_NOTE`, `SIMILARITY_NOTE`, `PRICE_NOTE` |
| A14 | **Forecast-language flag and standing disclaimer** on every answer. | Price-reporting integrity. The assistant never "estimates" a benchmark. | `finalize_answer` |
| A15 | **Guarded text-to-Cypher** (`graph_qa`): single statement, read-only, ontology-declared schema, forced LIMIT, row cap, READ transaction. Allowed **only for principals entitled to all data**, because generated Cypher can't be trusted to apply entitlement filters. | The riskiest tool gets several independent layers. | `graph_qa`, `guard_cypher` |
| A16 | **Validated history** (user/assistant roles only, bounded). | Blocks system-turn injection and context stuffing. | `clean_history` |
| A17 | **Traceability:** each answer returns its tool calls (args, outcome, ms), unverified ids and numbers, status and request id. | Reviewers can see where every fact came from. | `AgentAnswer`, `/api/chat` |
| A18 | **Behavioural evals**, including entitlement cases (public vs licensed), an injection planted in data, advice and emergency. | Regression gate for model, prompt and tool changes. | [evals/cases.json](evals/cases.json), [evals.py](energygraph/evals.py) |
| A19 | **Offline agent tests with a scripted model.** | Wiring, entitlement propagation, grounding and guardrails are tested in CI without an LLM. | [tests/test_agent.py](tests/test_agent.py) |
| A20 | **Pluggable local-first model backend** (Ollama or OpenAI-compatible gateway). | Licensed data stays inside approved infrastructure. | llm.py |

## 2. Data governance and entitlements

| # | Practice | Where |
|---|---|---|
| G1 | **Canonical record contract.** Every source maps to strict pydantic models (`extra="forbid"`, id patterns, enums, ranges, date ordering). | [sources/records.py](energygraph/sources/records.py) |
| G2 | **Source registry** with licence, attribution, refresh, allowed entitlements and record kinds. The loader rejects unregistered sources, foreign entitlements and unexpected kinds. | [sources/base.py](energygraph/sources/base.py), `check_governance` |
| G3 | **Organization sources can't choose their entitlement.** The connector imposes `org:<dataset>`, and a payload that tries to set `public` is quarantined. | `validate_record` |
| G4 | **Quarantine, not silent drop.** Invalid organization records go to `data/quarantine/<source>.jsonl` with errors and ids, never values. | `OrganizationSource._quarantine` |
| G5 | **Entitlement on every node AND relationship**, filtered in every query (`V()` and `R()`), including vector search, ownership chains, shortest paths and similarity. | [queries.py](energygraph/queries.py) |
| G6 | **No cross-entitlement overwrites.** Licensed facts about public assets become separate tagged relationships and nodes. Relationships are keyed by entitlement. | [etl/loader.py](energygraph/etl/loader.py) |
| G7 | **Stubs.** Nodes created only as reference targets are hidden from reads until their own record arrives, from any source, so load order doesn't matter. | `_stub`, `OWN` |
| G8 | **Similarity never mixes entitlements.** | [etl/similarity.py](energygraph/etl/similarity.py) |
| G9 | **Provenance everywhere.** `source` and `as_of` on nodes and relationships; `sources` with attribution in every response. | loader, `attribution()` |
| G10 | **Conservative entity linking.** GLEIF LEIs are linked only on exact normalised legal-name match. | [sources/public/gleif.py](energygraph/sources/public/gleif.py) |
| G11 | **Adversarial entitlement tests** across every tool, the API and per-dataset granularity. | [tests/test_entitlements.py](tests/test_entitlements.py) |

## 3. Authentication and authorisation

| # | Practice | Where |
|---|---|---|
| S1 | API keys (hashed SHA-256, shown once, `egk_` prefix) **and** OIDC JWT bearer tokens (JWKS signature, `exp`/`iss`/`aud`/`sub` required, leeway, algorithm allow-list). | [security/auth.py](energygraph/security/auth.py) |
| S2 | Scopes (`read`/`chat`/`admin`) are separate from data entitlements (`public`/`org:<dataset>`/`*`). | `Principal` |
| S3 | Constant-time key comparison, and rate-limited failed attempts. | `KeyStore`, `require()` |
| S4 | Key and OIDC config validated at startup; production refuses auth-disabled, missing credentials, default DB password, CORS `*`, the hash embedder, and non-https organization or JWKS URLs. | [config.py](energygraph/config.py) |

## 4. Limits and abuse protection

Token-bucket rate limits per principal + IP, with chat stricter. Concurrency gate for the agent (503 +
`Retry-After`). Body limit enforced on `Content-Length` and on the streamed byte count. Bounded query
parameters. Neo4j server-side query timeouts and a bounded pool. organization-source page-size, page-count
and retry caps. Tool-output and answer caps. See [security/ratelimit.py](energygraph/security/ratelimit.py)
and [api/security.py](energygraph/api/security.py).

## 5. Input validation and sanitisation

NFKC normalisation; zero-width, bidi and control characters removed; character allow-list for entities;
enum allow-lists (asset type, project status, incident type); ISO-3 and ISO-date validation; parameterised
Cypher only (labels come from validated literals); Lucene escaping; secrets stripped from HTTP cache keys;
validation errors never echo input; dashboard output escaped.

## 6. HTTP and deployment

Strict CSP (`script-src 'self'; style-src 'self'`, no inline code), `X-Frame-Options: DENY`, nosniff,
`Referrer-Policy`, `Permissions-Policy`, COOP, HSTS in production. CORS off by default. `X-Forwarded-For`
honoured only from trusted proxies. Docs disabled in production. Uniform error envelope with request ids.
Container: non-root, read-only filesystem, all capabilities dropped, `no-new-privileges`, resource limits,
healthcheck, ports bound to localhost.

## 7. Data access

A read-only client for the API and agent: local writes raise, and every query runs in a READ transaction,
which the server rejects writes in. Optional least-privilege DB account. Ontology contract: constraints and
vector indexes are generated from it; the validator checks labels, relationship types, subclass labels,
domain/range, and the presence of `source` and `entitlement` on every node and relationship. Synthetic seed
data is detected in production.

## 8. Observability and privacy

JSON logs with request ids. Audit log per chat records principal, entitlements, status, tools and
unverified items; questions are stored as a fingerprint unless `LOG_QUESTION_TEXT=true`. Prometheus
counters for requests, tool outcomes, rate limits, auth failures and unverified items, using route
templates to keep cardinality bounded. `/healthz` and `/readyz` probes.

## Known limits

* Rate limits and the concurrency gate are per process. Behind several replicas, move them to Redis or the gateway.
* Neo4j Community has no row-level security. Entitlements are enforced in every query (and tested); Enterprise
  RBAC can add account-level separation.
* Vector search oversamples, then filters by entitlement. Very rare licensed-dense neighbourhoods may return
  fewer than `k` public hits.
* Keyword guardrails (emergency, advice, injection) are a first layer, and the evals measure them.
* WRI GPPD is no longer updated (2021). Its `as_of` dates say so.
