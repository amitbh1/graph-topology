# MedGraph

A local medical intelligence platform that connects fragmented public medical data into a
**Neo4j knowledge graph** and makes it explorable through **graph-assisted search**, **GraphRAG**
and a **grounded LangGraph assistant**. Everything (graph, ETL, embeddings, LLM, web app) runs locally.

> Research prototype over public data. **Not medical advice.** FAERS adverse event reports are
> reported associations, not proof of causation. Drug similarity is exploratory, not a recommendation.

## Architecture

```
 Public sources                     ETL (Python)                 Neo4j graph                  Access
 ─────────────                      ────────────                 ───────────                  ──────
 WHO ATC/DDD  ───────────┐   atc.py  ─ 5-level tree                                    ┌─ FastAPI  /api/*
 MeSH 2026 (NLM XML) ────┤   mesh.py ─ multi-parent hierarchy     16 node classes      ├─ Dashboard  /
 openFDA labels ─────────┤   openfda_labels.py ─ docs, indications 20 rel types        ├─ CLI  python -m medgraph
 openFDA FAERS ──────────┼─► faers.py ─ reports → reactions → MeSH 14 vector indexes ──┤
 ClinicalTrials.gov v2 ──┤   clinical_trials.py ─ trials ↔ MeSH    fulltext indexes    └─ LangGraph ReAct agent
 RxNorm / RxNav ─────────┤   rxnorm.py ─ brand ↔ ingredient                                (9 tools, grounding)
 NLM Clinical Tables ────┘   icd10.py ─ ICD-10-CM
                             similarity.py ─ Cypher-only analytics
                             embed.py ─ nomic-embed-text → vectors
                ▲
                └──────── ontology/medical_graph.ttl  (schema contract for ETL, API, assistant, tests)
```

| Component | Where |
|---|---|
| Medical vocabulary (ATC, MeSH) | [medgraph/etl/atc.py](medgraph/etl/atc.py), [medgraph/etl/mesh.py](medgraph/etl/mesh.py) |
| Graph-assisted search | `mesh_expand`, `search_documents` in [medgraph/queries.py](medgraph/queries.py) |
| Drug safety (FAERS) | [medgraph/etl/faers.py](medgraph/etl/faers.py), `drug_safety` |
| Clinical trial intelligence | [medgraph/etl/clinical_trials.py](medgraph/etl/clinical_trials.py), `trials_pipeline` |
| Drug identity (RxNorm, ICD-10-CM) | [medgraph/etl/rxnorm.py](medgraph/etl/rxnorm.py), [medgraph/etl/icd10.py](medgraph/etl/icd10.py) |
| Graph analytics (similarity) | [medgraph/etl/similarity.py](medgraph/etl/similarity.py) |
| GraphRAG | [medgraph/embeddings.py](medgraph/embeddings.py), `semantic_search` |
| AI assistant | [medgraph/agent/](medgraph/agent/) |
| Ontology & validation | [ontology/medical_graph.ttl](ontology/medical_graph.ttl), [medgraph/ontology.py](medgraph/ontology.py), [medgraph/validate.py](medgraph/validate.py) |

## Quick start

Requirements: Docker, Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows   (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
cp .env.example .env              # already present if you cloned this folder

docker compose up -d              # Neo4j (7474/7687) + Ollama (11434)
docker exec medgraph-ollama ollama pull nomic-embed-text
docker exec medgraph-ollama ollama pull qwen2.5:7b      # any model with tool calling
```

### Option A — offline sample graph (seconds)

```bash
python -m medgraph seed           # illustrative sample around the Glucotrol/Glipizide walkthrough
```

The seed uses real vocabulary identifiers but **synthetic** reports (`SEED-AE-*`) and trials
(`NCT-SEED-*`). It goes through the same load queries as the real ETL. Set `EMBEDDING_BACKEND=hash`
if Ollama is not running.

### Option B — real public data

```bash
python -m medgraph etl all
# or step by step:
python -m medgraph etl schema atc mesh labels rxnorm atc-link faers trials icd10 similarity embed
```

Sample sizes are set in `.env` (`ETL_LABEL_LIMIT=250`, `ETL_FAERS_PER_DRUG`, `ETL_TRIAL_CONDITIONS`,
`ETL_TRIALS_PER_CONDITION`). HTTP responses are cached in `data/cache/`, so re-runs are fast and every
step is idempotent (`MERGE`). Embedding the full graph (~270k nodes) with `nomic-embed-text` takes a
while on CPU; `python -m medgraph etl embed` resumes where it stopped.

> **Don't mix the two.** Clear the database before switching from seed to real data:
> `docker exec medgraph-neo4j cypher-shell -u neo4j -p medgraph-local "MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS"`

### Use it

```bash
python -m medgraph serve          # dashboard + API → http://127.0.0.1:8000  (OpenAPI docs at /docs)
python -m medgraph profile glucotrol
python -m medgraph ask "What does MedGraph know about Glucotrol?"
python -m medgraph chat
python -m medgraph validate       # ontology ↔ code ↔ database
pytest                            # unit tests; integration tests run when a loaded Neo4j is reachable
```

Neo4j Browser: http://localhost:7474 (neo4j / medgraph-local).

## Production deployment

Security is on by default. [best-practice.md](best-practice.md) lists every control, and
[changes.md](changes.md) records the decisions behind them.

```bash
python -m medgraph apikey alice --scopes read,chat     # key shown once; add the printed entry to MEDGRAPH_API_KEYS
python -m medgraph apikey ops --scopes admin
# .env: MEDGRAPH_ENV=production, a strong NEO4J_PASSWORD, MEDGRAPH_API_KEYS=..., CORS_ORIGINS=...
python -m medgraph check-config                        # must print OK — the API refuses to start otherwise
docker compose --profile api up -d --build             # non-root, read-only container on 127.0.0.1:8000
python -m medgraph eval                                # behavioural regression suite for the assistant
```

Clients send `X-API-Key: <key>` (or `Authorization: Bearer <key>`). The dashboard has an API-key field.
Scopes: `read` (graph endpoints), `chat` (assistant), `admin` (`/api/stats`, `/metrics`).
Probes: `/healthz` (liveness), `/readyz` (Neo4j). Put a TLS-terminating reverse proxy in front of the API, and list it in
`TRUSTED_PROXIES` so client IPs (used for rate limits) are taken from `X-Forwarded-For`.

The local development `.env` sets `MEDGRAPH_AUTH_DISABLED=true`. Production refuses that setting.

## How the pieces work

### Vocabulary
* **ATC** — every level is its own `:ATCLevel` node: `(:ATCLevel {code:'A10BB07'})-[:ATC_PARENT]->(A10BB)->…->(A)`.
  Drugs attach to their level-5 code with `CLASSIFIED_AS`.
* **MeSH** — descriptors form a *multi-parent* graph: the parent of tree number `C18.452.394.750` is the
  descriptor at `C18.452.394`. Diabetes Mellitus therefore has two parents (Nutritional and Metabolic
  Diseases, Endocrine System Diseases). Every synonym (including the preferred name) is a `:MeshEntryTerm`.

### Graph-assisted search
`term → MeSH concept → descendants via MESH_PARENT* → all synonyms → fulltext search of label documents`.
The response reports keyword-only vs graph-expanded match counts and the recall gain.

### Drug identity
openFDA generic names (often salts, e.g. *metformin hydrochloride*) are resolved through RxNav to the
RxNorm ingredient (*metformin*), which becomes the canonical `:Drug`. Brand names and salt forms become
`:DrugAlias` nodes (`ALIAS_OF`) — an alias is never a drug in its own right.

### Safety, trials, ICD-10
`(:Drug)-[:SUSPECT_IN]->(:AdverseEventReport)-[:HAS_REACTION]->(:Reaction)-[:REACTION_MAPS_TO]->(:MeshDescriptor)`.
MedDRA's British spellings (*hypoglycaemia*) are normalised before matching MeSH (*hypoglycemia*).
Trials link to MeSH through ClinicalTrials.gov's own condition-browse MeSH ids, and drug interventions
resolve to canonical drugs. Active disease concepts get ICD-10-CM codes from NLM Clinical Tables.

### Indication extraction
Indications are dictionary-matched against MeSH disease entry terms (longest match), after dropping
negated sentences (*"Not for treatment of type 1 diabetes"*). Like any label extraction it is imperfect —
treat `INDICATED_FOR` as "mentioned as an indication on the label", and check the source document.

### Similarity (Cypher only, no GDS plugin)
`score = shared_indications × 2 + shared_reactions`, materialised as `SIMILAR_TO` relationships.

### GraphRAG
Each ontology class with `mg:embedText` gets an embedding and a vector index (14 in total).
`semantic_search` finds the nearest concepts by meaning ("high blood sugar condition" → Hyperglycemia),
then traverses the graph from the best MeSH hit for drugs, reactions, trials and codes.
The `hash` embedding backend is a lexical fallback for offline use — it is not semantic.

### Assistant
LangGraph ReAct agent (`create_react_agent`) with nine tools: `disease_overview`, `mesh_expand`,
`search_documents`, `drug_safety`, `trials_pipeline`, `decode_atc`, `similar_drugs`, `semantic_search`,
`graph_qa`. Backend is pluggable: `LLM_BACKEND=ollama` or `LLM_BACKEND=openai` (any OpenAI-compatible
server, e.g. a local OpenCode API). Use a model with real tool-calling support.

Grounding measures:
1. **Deterministic tools** for common multi-step questions (e.g. `disease_overview` walks
   concept → narrower concepts → drugs → reactions → trials → ICD-10 in one call).
2. **Grounding rules** in the system prompt: report only tool results; say so when the graph has no answer.
3. **Identifier check** — any NCT / ATC / report / ICD-10 id in the answer that never appeared in a tool
   result is removed and flagged.
4. **`graph_qa` guard** — generated Cypher must be a single read-only statement that uses only
   ontology-declared labels and relationship types; it runs in a READ transaction with a LIMIT.
5. Tool arguments are sanitised (`"drug: Glucotrol"` → `Glucotrol`) and every entity is resolved
   (exact → alias → fuzzy) before querying.

### Ontology
[ontology/medical_graph.ttl](ontology/medical_graph.ttl) declares 16 classes, 20 relationship types,
key properties and domains/ranges. It drives constraint creation, vector indexes, the `graph_qa` schema
prompt and validation. `python -m medgraph validate` and the tests check that:
* every node label and relationship type used in Cypher anywhere in the code is declared
  (an `INDICATION_FOR` typo fails the build),
* every relationship's domain and range is a declared class,
* database labels, relationship types and uniqueness constraints match the ontology,
* every relationship instance in the database respects its declared domain/range.

It is deliberately lightweight: a development-time contract, not an OWL reasoner (no cardinality,
disjointness or runtime validation).

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/stats` | node / relationship / vector index / embedding counts |
| `GET /api/drug/{name}` | full profile: identity, ATC, indications, safety, similar drugs, trials |
| `GET /api/drug/{name}/safety`, `/similar` | FAERS summary; similarity ranking |
| `GET /api/atc/{drug_or_code}` | decoded ATC path |
| `GET /api/disease/{name}` | disease overview |
| `GET /api/mesh/expand?term=` | MeSH expansion |
| `GET /api/search?term=&expand=` | graph-assisted document search |
| `GET /api/trials?condition=&drug=&status=` | trial pipeline |
| `GET /api/semantic?text=` | vector search + graph context |
| `POST /api/chat` `{question, history}` | grounded assistant |
| `GET /api/ontology` | declared schema |

## Data sources

WHO Collaborating Centre for Drug Statistics Methodology — ATC/DDD Index (via the
[fabkury/atcd](https://github.com/fabkury/atcd) extraction) · US National Library of Medicine — MeSH 2026 ·
openFDA — drug labels and FAERS · ClinicalTrials.gov API v2 · RxNorm / RxNav · NLM Clinical Tables — ICD-10-CM.
Check each source's terms of use before redistributing derived data.
