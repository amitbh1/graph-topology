# Changes and decisions

A record of what was built or changed, and **why** each decision was made.
Newest first. Every practice is described in detail in [best-practice.md](best-practice.md).

---

## v1.1.0 — Production hardening (2026-11-01)

### Added
| Area | Change |
|---|---|
| Security package | `medgraph/security/` — `auth.py` (hashed API keys, scopes), `ratelimit.py` (token bucket, concurrency gate), `sanitize.py` (text/entity/status sanitisers) |
| API security | `medgraph/api/security.py` — `require(scope)` dependency, trusted-proxy client IP, ASGI middleware (request id, body limit, security headers, access log, metrics) |
| API | Rewritten `medgraph/api/main.py`: bounded/validated parameters, uniform error envelope, `/healthz`, `/readyz`, `/metrics`, docs off in production, startup config check |
| Guardrails | `medgraph/agent/guardrails.py` — question/history validation, crisis short-circuit, injection neutralisation, answer cap + disclaimer |
| Tools | Pydantic argument schemas, per-request tool budget, error isolation, untrusted-data envelope, per-call trace |
| Agent | Overall deadline, recursion limit tied to the budget, audit logging, `status` field (ok/crisis/timeout/error) |
| DB | `get_read_client()` (read-only, server timeouts), connection-pool limits, optional `NEO4J_READ_USER` |
| Config | Validated settings with `problems()`; production refuses unsafe values; new `check-config` CLI |
| CLI | `apikey` (generate key + hashed entry), `check-config`, `eval` |
| Evals | `evals/cases.json` + `medgraph/evals.py` — behavioural regression suite |
| Observability | `medgraph/observability.py` — JSON logs, request ids, Prometheus counters, question fingerprinting |
| Deployment | `Dockerfile` (non-root, healthcheck), `.dockerignore`, hardened `docker-compose.yml` (`api` profile, localhost ports, read-only FS, dropped caps, limits) |
| Dashboard | JS moved to `web/app.js` (strict CSP), API-key field (kept in sessionStorage only) |
| Tests | `tests/test_security.py`; `tests/test_agent.py` extended — 66 tests in total |
| Docs | `best-practice.md`, this file |

### Decisions

**API keys with scopes rather than OAuth/JWT.**
The platform runs locally or behind a private gateway, so there is no identity provider to integrate with.
Hashed, scoped keys are simple to operate and to rotate (by editing an env entry), and they can be audited per key.
The `require(scope)` dependency is the only integration point. Swapping in JWT/OIDC later is a local change.
*Rejected:* basic auth (passwords in every request, no scopes); session cookies (CSRF surface, and not needed for an API).

**Store only key hashes and compare in constant time.**
A leaked config or log must not leak usable credentials. Comparing against every key avoids leaking timing information about which key prefix matched.

**Separate `chat` scope and a stricter chat rate limit.**
The assistant calls an LLM and is 10–100× more expensive than a graph query. Read-only integrations should not be able to spend that budget.

**In-process rate limiting and concurrency gate.**
There are no extra moving parts for a single-node deployment. The limitation for multi-replica deployments (use Redis or the gateway) is documented rather than hidden.

**Fail-fast production config.**
Insecure defaults are convenient in development and dangerous in production. Refusing to start is safer than warning.
The dev `.env` sets `MEDGRAPH_AUTH_DISABLED=true`; `.env.example` is secure by default.

**Read-only enforcement at three layers** (client refuses writes → READ transaction → server rejects writes).
The assistant can generate Cypher (`graph_qa`). Even if every prompt-level defence failed, the database must not accept a write.
Verified live: Neo4j returns `Neo.ClientError.Statement.AccessMode`.

**Keep `graph_qa`, but guard it heavily and make it switchable.**
It answers long-tail questions that no fixed tool covers. Given the risk, it is the last-resort tool and goes through layered validation (A18).
It can be disabled per deployment with `ENABLE_GRAPH_QA=false`.

**Tool budget returns a message instead of raising.**
A raised error ends the run with nothing. A message lets the model answer from the evidence it already has, which degrades gracefully.

**Tool results wrapped as `untrusted_data`, plus phrase neutralisation.**
Prompt injection through retrieved data is the main agent-specific risk in RAG. Wrapping and labelling is the primary defence; regex neutralisation is a cheap second layer. Neither is perfect, which is why writes are impossible anyway.

**Deterministic crisis short-circuit.**
For overdose or self-harm messages the right answer is known and must not depend on model quality. A keyword detector errs toward over-triggering; a false positive costs one extra safety message.

**Privacy-preserving audit logs.**
Questions to a medical assistant can contain health information. By default, logs store a fingerprint and length. Text logging is an explicit opt-in (`LOG_QUESTION_TEXT`).

**Validation errors never echo input.**
Echoed values can reflect injected content into clients and logs. The field name and the reason are enough to fix a request.

**Dashboard JavaScript moved out of the HTML.**
This allows `script-src 'self'` (no inline scripts), which removes the main XSS escalation path. `style-src` still allows inline styles; the risk is low and noted.

**Behavioural evals in addition to unit tests.**
Unit tests can't tell whether a new model or prompt still chooses the right tools or resists injection. The eval suite is the regression gate for agent behaviour.

**LangGraph `create_react_agent` kept, pinned `langgraph>=1.0,<2`.**
The spec requires a LangGraph ReAct agent. The function is deprecated in favour of `langchain.agents.create_agent`, but it is supported until v2. Pinning the major version avoids a surprise break.

### Data fixes made during this round
| Problem found | Fix | Decision |
|---|---|---|
| ICD-10 step crashed on a Windows cache write (`Errno 22`) | HTTP cache writes are atomic (temp file + replace) and best-effort | A cache is an optimisation; it must never stop the pipeline |
| ICD-10 targeted 574 "active" diseases (trial conditions included) | Only diseases that drugs are indicated for, and skip ones already mapped | Matches the spec's intent ("active disease concepts"), avoids re-querying, and makes reruns incremental |
| Glipizide had only 1 FAERS report | Oversample ×10 and keep only reports where the drug is really a *suspect* | openFDA can't filter on the suspect role per drug entry; about 15% of hits are suspect reports (measured: 15 of 100 for glipizide). Result: glipizide now has 12 reports (8 serious, 0 deaths, 0 hospitalisations) and 99 similar drugs (spec: 7 reports, 105 similar). Graph: 314,120 nodes, 342,700 relationships, 8,626 `SIMILAR_TO` |
| openFDA query built by string formatting | `lucene_value()` quoting | A drug name containing quotes or operators could otherwise change the query |

---

## v1.0.0 — Initial MedGraph build (2026-11-01)

### Built
* **Ontology** `ontology/medical_graph.ttl`: 16 classes, 20 relationship types (the 19 described in the spec plus `SIMILAR_TO`), key properties, `embedText` annotations that drive the 14 vector indexes.
* **ETL** for WHO ATC, MeSH 2026, openFDA labels, FAERS, ClinicalTrials.gov v2, RxNorm/RxNav and ICD-10-CM; Cypher-only similarity; embeddings; an offline seed dataset.
* **Query layer** `medgraph/queries.py`, shared by the API, dashboard and assistant.
* **LangGraph assistant** with the 9 tools from the spec and grounding checks.
* **FastAPI + dashboard**, CLI, ontology validator, tests.

### Real-data results
| Metric | Spec | Built |
|---|---|---|
| MeSH descriptors | 31,110 | 31,110 |
| MeSH hierarchy relationships | 42,519 | 42,519 |
| "Diabetes Mellitus" expansion | 281 terms | 281 terms |
| Expanded-search recall gain | +67% (6 → 10, 250-document sample) | +14% (42 → 48, different 250-document sample) |
| Glucotrol → Glipizide → A10BB07 | yes | yes |
| Vector indexes | 14 | 14 |

### Decisions
* **MeSH from the XML file.** The 2026 ASCII release is not published. NLM also serves `desc2026.gz` uncompressed, so gzip is detected by magic bytes, not by extension.
* **ATC from the `fabkury/atcd` extraction of the WHO index.** WHO has no bulk download. The URL is configurable.
* **Canonical drug = RxNorm ingredient.** Labels say "metformin hydrochloride"; the ATC index and FAERS say "metformin". A salt resolves to its single ingredient. A multi-ingredient (MIN) name is used only for real combinations. This fixed a bug where metformin resolved to "metformin / saxagliptin".
* **Brand names and salts are `DrugAlias` nodes, never drugs** (an explicit ontology rule), so counts and similarity aren't split across names.
* **The preferred name is stored as an entry term**, so every name lookup takes one path (entry-term count: 267,012 links).
* **Indications by MeSH dictionary matching with negation filtering.** Without the filter, "Not for type 1 diabetes" became an indication.
* **MedDRA → MeSH with British→American spelling normalisation** ("hypoglycaemia" → "hypoglycemia").
* **Relationship-pattern extractor requires a leading `-[`**, so Python slices like `text[:MAX]` aren't flagged as Cypher.
* **Seed data uses real vocabulary ids but synthetic reports and trials** (`SEED-AE-*`, `NCT-SEED-*`). It never presents invented records as real.
* **The `hash` embedder exists only as an offline fallback.** It is lexical, not semantic, and production config refuses it.
