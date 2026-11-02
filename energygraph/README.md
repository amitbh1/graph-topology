# EnergyGraph

An energy-intelligence knowledge graph with a grounded AI assistant. It connects public energy datasets
(and, when integrated, **organization-source** licensed datasets) in Neo4j, and exposes them through
taxonomy-assisted search, GraphRAG, an entitlement-aware REST API and a LangGraph agent.

> Reports published data with sources and as-of dates. **Not investment, trading or operational advice.**
> EnergyGraph never forecasts or estimates prices.

## What it answers

* *What does EnergyGraph know about Harbor Point LNG?* Identity, owners and shares, operator, capacity,
  fuels/technologies, incidents, expansion projects, network links, documents and sources.
* *Who ultimately owns this asset?* Ownership and parent chains, using GLEIF LEIs where they resolve exactly.
* *How does gas get from this field to that terminal?* Physical paths with link capacities.
* *Which LNG projects in the USA reached FID?* The project pipeline with capacity totals by status.
* *Which plants are peers of this one?* An explained similarity score.
* *Show the Henry Hub price over the last 90 days.* Published observations and summaries only.
* *Convert 15 mtpa of LNG to bcm/y.* Deterministic conversion with stated assumptions.

## Architecture

```
 Sources                                Ingestion                         Stores                      Access
 ───────                                ─────────                         ──────                      ──────
 Open Energy Ontology (CC0) ──┐   connectors emit CANONICAL RECORDS   ┌─ Neo4j graph           ┌─ REST API (API keys / OIDC,
 WRI Global Power Plant DB ───┤   (pydantic, strict)                  │  18 classes, 22 rels   │   scopes, entitlements)
 Wikidata refineries + LNG ───┤        │                              │  11 vector indexes     ├─ Dashboard
 GLEIF LEI (CC0) ─────────────┼──►  loader: registry + entitlement ──►├─ Time-series store     ├─ LangGraph agent (14 tools)
 US EIA prices (API key) ─────┤   governance, no cross-entitlement    │  (published prices)    └─ CLI, evals, metrics
 organization-source ─────────┘   overwrites, quarantine              └─ HTTP cache / raw files
   (file | https, masked)                 ▲
                                          └── ontology/energy_graph.ttl  (schema + entitlement contract)
```

| Area | Where |
|---|---|
| Canonical record contract | [energygraph/sources/records.py](energygraph/sources/records.py) |
| Connector interface + registry | [energygraph/sources/base.py](energygraph/sources/base.py) |
| Public connectors | [energygraph/sources/public/](energygraph/sources/public/) |
| organization-source connector | [energygraph/sources/organization/](energygraph/sources/organization/) and [ORGANIZATION_SOURCE.md](ORGANIZATION_SOURCE.md) |
| Loader (governance) | [energygraph/etl/loader.py](energygraph/etl/loader.py) |
| Entitlement-filtered queries | [energygraph/queries.py](energygraph/queries.py) |
| Assistant | [energygraph/agent/](energygraph/agent/) |
| API + security | [energygraph/api/](energygraph/api/), [energygraph/security/](energygraph/security/) |
| Units | [energygraph/units.py](energygraph/units.py) |
| Ontology + validator | [ontology/energy_graph.ttl](ontology/energy_graph.ttl), [energygraph/validate.py](energygraph/validate.py) |

## Data sources

| Source | Content | Licence | Status |
|---|---|---|---|
| Open Energy Ontology | Fuels, technologies, energy carriers (multi-parent taxonomy, synonyms) | CC0 1.0 | built in |
| WRI Global Power Plant Database v1.3 | Power plants ≥ `GPPD_MIN_MW` with owners, fuels, coordinates | CC BY 4.0 (final release 2021) | built in |
| Wikidata | Oil refineries and LNG terminals with operators and owners | CC0 1.0 | built in |
| GLEIF | Legal entities, LEIs, direct parents (exact-name matching only) | CC0 1.0 | built in |
| US EIA Open Data | WTI, Brent, Henry Hub spot prices | US public domain | needs free `EIA_API_KEY` |
| EnergyGraph core | Concepts not covered by OEO (cogeneration, storage, …) | internal | built in |
| organization-source (12 datasets) | Licensed assets, outages, projects, prices, news, taxonomy | organization licence | interface ready, masked |
| Synthetic seed | Fictional companies and assets for demos and tests | synthetic | `energygraph seed` (never in production) |

Run `python -m energygraph sources` to see each source's availability and licence.

## Quick start

```bash
cd energygraph
python -m venv .venv && .venv\Scripts\activate          # source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
cp .env.example .env                                     # dev: set ENERGYGRAPH_AUTH_DISABLED=true if you want
docker compose up -d                                     # Neo4j (7475/7688) + Ollama (11435), localhost only
docker exec energygraph-ollama ollama pull nomic-embed-text
docker exec energygraph-ollama ollama pull qwen2.5:7b    # any model with tool calling

python -m energygraph seed                               # synthetic world + synthetic organization samples
python -m energygraph serve                              # http://127.0.0.1:8100  (docs at /docs in dev)
python -m energygraph ask "Who ultimately owns Harbor Point LNG?"
python -m energygraph ask "Tell me about Kestrel Bay Power Station" --entitlements public,org:power-assets
pytest                                                   # 73 tests; DB tests need the seeded graph
```

Set `EMBEDDING_BACKEND=hash` if Ollama isn't running. It is a lexical fallback for offline demos and tests,
and production config refuses it.

### Real public data
```bash
python -m energygraph etl schema energygraph-core oeo gppd wikidata gleif eia similarity embed
python -m energygraph validate
```
Use a separate database for real data. The synthetic seed must never be loaded in production, and
`validate` flags it there.

## Security model

* **Authentication:** hashed API keys (`egk_…`, shown once) or OIDC JWTs. JWTs are checked for signature
  (JWKS), `exp`, `iss`, `aud` and `sub`.
* **Authorisation:** scopes control actions (`read`, `chat`, `admin`). Entitlements control data
  (`public`, `org:<dataset>`, `*`). Every node and relationship carries an entitlement, and every query
  filters on it.
* **Limits:** per-principal rate limits (stricter for chat), a concurrency gate, body-size limit, bounded
  parameters, Neo4j query timeouts, and agent deadline, tool budget and output caps.
* **Data integrity:**
  - connectors must be registered;
  - a public source cannot emit licensed data, and an organization source cannot emit public data;
  - licensed records cannot overwrite public nodes;
  - invalid organization records are quarantined.
* **Agent safety:**
  - read-only tools;
  - tool results are wrapped as untrusted data, with injection phrases neutralised;
  - ids and numbers in answers are checked against tool output;
  - forecast wording is flagged;
  - emergency and trading-advice messages get deterministic responses;
  - `graph_qa` is available only to principals entitled to all data.

Full list: [best-practice.md](best-practice.md). Decisions: [changes.md](changes.md).

## Production

```bash
python -m energygraph apikey analyst --scopes read,chat --entitlements public,org:power-assets
python -m energygraph apikey ops --scopes admin --entitlements public
# .env: ENERGYGRAPH_ENV=production, strong NEO4J_PASSWORD, ENERGYGRAPH_API_KEYS and/or OIDC_*, CORS_ORIGINS
python -m energygraph check-config        # must print OK; the API refuses to start otherwise
docker compose --profile api up -d --build
python -m energygraph eval                # behavioural regression suite (needs graph + LLM)
```
Probes: `/healthz`, `/readyz`. Metrics: `/metrics` (admin scope). Put TLS termination in front, and set
`TRUSTED_PROXIES` so the rate limiter sees real client IPs.

## API

| Endpoint | Scope | Purpose |
|---|---|---|
| `GET /api/me` | read | Caller identity, scopes, entitlements |
| `GET /api/sources` | read | Source catalog with an entitled flag per source |
| `GET /api/asset/{name}` · `/similar` | read | Asset overview · peers |
| `GET /api/ownership/{name}` | read | Ownership chain (company or asset) |
| `GET /api/incidents?entity=&start=&end=&incident_type=` | read | Incident history |
| `GET /api/projects?country=&asset_type=&concept=&company=&status=` | read | Project pipeline |
| `GET /api/path?origin=&destination=` | read | Supply path / network neighbours |
| `GET /api/prices?series=&start=&end=` | read | Published price observations and summary |
| `GET /api/convert?value=&from_unit=&to_unit=&commodity=&api_gravity=` | read | Unit conversion |
| `GET /api/taxonomy/expand` · `/decode` | read | Taxonomy expansion · classification path |
| `GET /api/search?term=` · `/api/semantic?text=` | read | Document search · GraphRAG |
| `POST /api/chat` | chat | Grounded assistant |
| `GET /api/stats`, `/metrics` | admin | Operations |
