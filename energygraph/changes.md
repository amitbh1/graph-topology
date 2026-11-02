# Changes and decisions

## v1.0.0 — Initial EnergyGraph build (2026-11-02)

Built from the MedGraph architecture and the EnergyGraph concept. All solution files live in this folder.
The organization and its products are masked as **organization-name** / **organization-source**.

### Built
* **Ontology** `ontology/energy_graph.ttl`: 18 classes (12 root classes, plus 6 asset subtypes that also carry
  `:Asset`) and 22 relationship types. Every node and relationship requires `source` and `entitlement`.
* **Canonical record contract** and **connector framework**, with 6 built-in sources (OEO, WRI GPPD, Wikidata,
  GLEIF, EIA, core) plus a synthetic seed.
* **organization-source**: 12 masked datasets, file and HTTPS transports, contract validation, quarantine,
  CLI (`org validate|status|ingest`), synthetic sample payloads including deliberately invalid rows.
* **Loader** with governance (registry, entitlement and kind checks, no cross-entitlement overwrite, stubs).
* **Time-series store** interface + SQLite implementation for published prices.
* **14-tool LangGraph agent** with entitlement propagation, number grounding, deterministic unit conversion,
  emergency and advice short-circuits.
* **API** with API keys + OIDC, scopes + entitlements, rate limits and hardening; dashboard; Docker; evals;
  73 tests.

### Verified
* Seed + organization samples load; exactly the 3 deliberately invalid sample records are quarantined.
* `validate` reports OK against the live database (contract, entitlement and provenance checks).
* Public principals see no organization data through any tool or endpoint; licensed principals do; access
  is granted per dataset.
* Live public connectors (2026-11-02):

  | Source | Loaded |
  |---|---|
  | Open Energy Ontology v2.14.0 | 1,382 concepts |
  | WRI GPPD (≥ 50 MW) | 10,701 power plants, 3,459 owner companies |
  | Wikidata | 396 refineries and LNG terminals, 118 operators/owners |
  | GLEIF | 113 company records: LEIs plus direct parents (exact-match only) |
  | EIA | skipped — no `EIA_API_KEY` (by design) |
  | Derived | 92,433 `SIMILAR_TO` (top-10 peers per asset), 16,289 embeddings (hash backend for the test run) |

  Graph: 16,369 nodes, 125,545 relationships, 11 vector indexes, 0 unresolved stubs. `validate` OK, and 73/73
  tests pass with real data loaded. Spot checks: Grand Coulee → 6,809 MW, hydro, U S Bureau of Reclamation;
  Hydro-Québec → parent Province of Québec, 49 assets; "renewable energy" expands to 2,244 power plants.

### Decisions

**Canonical records between sources and the graph.**
Every source — public now, organization later — maps to one validated contract, and only the loader writes
Cypher. Integrating an organization dataset is then configuration plus a mapping, and governance can't be
skipped by a new connector. *Rejected:* a connector per dataset writing its own Cypher (governance would be
duplicated and drift).

**Entitlements as data on every node AND relationship, filtered in every query.**
Licensed facts are often *about* public things: an outage on a public plant, or an ownership share of a public
company. Node-level tagging alone would leak those relationships. Neo4j Community has no row-level security,
so the filter lives in the query layer and an adversarial test suite enforces it. *Rejected:* separate
databases per entitlement (cross-dataset questions become impossible); post-filtering answers (too late —
the model has already seen the data).

**The connector imposes the organization entitlement; the payload can't set it.**
A feed must never be able to publish its own data as public, whether by mistake or on purpose.

**No cross-entitlement overwrites, with stubs for forward references.**
If licensed and public values disagree about a public asset, overwriting would silently relicense public
users' view, or leak licensed values. Licensed facts become separate tagged facts. Stubs make loading
independent of order (a bug found during the build: outages arriving before their asset).

**`graph_qa` only for all-data principals.**
LLM-generated Cypher can't be trusted to apply entitlement filters. Partially entitled users get the 13
deterministic tools.

**Number grounding in addition to identifier grounding.**
In energy, a hallucinated capacity or price is the main risk. Numbers ≥ 10 or with decimals must match tool
output within rounding. Years and small counts are exempt to avoid noise.

**Deterministic unit conversion with stated assumptions.**
Conversions like crude t↔bbl and LNG t↔MMBtu depend on the commodity. The tool reports the factor and the
assumption (e.g. API gravity), so the answer can state it.

**Deterministic short-circuits for emergencies and trading advice.**
These are safety and compliance boundaries, and must not depend on model behaviour.

**GLEIF linking only on exact normalised legal names.**
A wrong ownership link is worse than a missing one; fuzzy matches are discarded.

**OIDC in addition to API keys.**
Enterprise users authenticate through SSO, and entitlements arrive as token claims. API keys remain for
service accounts. Both produce the same `Principal`, so authorisation code is shared.

**Separate ports, container names and database from MedGraph.**
Both stacks can run side by side (7475/7688/11435/8100).

**SQLite time-series store behind an interface.**
Runnable with no extra infrastructure. Production volumes should implement `SeriesStore` over a dedicated
series platform.

### Issues found and fixed during the build
| Issue | Fix |
|---|---|
| Neo4j image rejected `server.memory.pagecache.size` and crash-looped, corrupting the new volume | Removed the setting; recreated the empty volume |
| `point({{…}})` braces in a non-f-string broke the asset load query | Single braces |
| Organization asset referenced by an outage before the asset loaded stayed a hidden stub | Any record may take over a stub (stubs carry only an id) |
| API shutdown closed the shared read client, breaking the next app instance | `close_read_client()` resets it |
| Tests reloading the auth module split class identity across test files | Patch the settings reference instead of reloading the module |
