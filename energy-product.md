# EnergyGraph — an energy-intelligence knowledge graph and grounded AI assistant

**A product concept for S&P Global Energy**, adapted from the MedGraph architecture
([product-medical.md](product-medical.md), implementation in this repository).

> Status: concept / solution design. Dataset names marked *(S&P internal — confirm)* are candidates to be
> validated with the data owners for availability, licensing and entitlement models. Example facts in the
> walkthrough are illustrative and would be answered from the graph, not from this document.

---

## 1. The problem

Energy information is spread across many different sources:

* **Price assessments** describe what a commodity was worth at a location and time.
* **Asset databases** describe power plants, refineries, LNG terminals, pipelines and fields.
* **Outage and incident reports** record what went wrong and when.
* **Project trackers and interconnection queues** show what is being built.
* **Regulatory filings and company disclosures** show who owns what and what they plan.
* **Trade and vessel-flow data** show how molecules move between markets.
* **Classification systems** define commodities, grades, technologies and industries.

Finding the information is not the hard part. Connecting it is. Typical questions:

* Which assets does a company own, directly or through subsidiaries, and in which markets?
* Which refineries can run a given crude grade, and which of them reported outages last quarter?
* Which pipelines and terminals connect a producing basin to an export market?
* Which proposed LNG or power projects compete for the same market, and how far along are they?
* Which plants are most similar to this one by fuel, technology, size, age and outage profile?
* Can one question about "Sabine Pass" be answered from all of these at once, with every fact traceable?

**EnergyGraph** connects these sources into a knowledge graph. It exposes them through graph-assisted
search, GraphRAG and a grounded LangGraph assistant, using the same patterns proven in MedGraph.

---

## 2. MedGraph → EnergyGraph mapping

The architecture carries over component by component. Only the domain changes.

| MedGraph component | Role | EnergyGraph equivalent |
|---|---|---|
| **MeSH** (multi-parent concept hierarchy + synonyms) | Common vocabulary | **Energy concept and commodity taxonomy**: Open Energy Ontology (OEO), UN SIEC energy-product classes, Platts commodity/grade specifications. A grade such as "WTI Midland" sits under *Crude oil → Light sweet* **and** under *US Gulf Coast export grades*. |
| **ATC** (strict 5-level tree) | Classification | **Asset / technology / industry trees**: asset type → fuel → technology → prime mover (EIA codes); NAICS / ISIC / GICS for companies; HS codes for traded products. |
| **RxNorm** (brand ↔ ingredient) | Identity resolution | **Entity resolution**: company legal entity ↔ brands ↔ parent (GLEIF LEI, corporate hierarchies); asset aliases (plant names, EIA plant IDs, ENTSO-E EIC codes, GEM IDs); grade aliases ("Dated Brent" / "Brent" / "BFOET"). |
| **ICD-10-CM** (code crosswalk) | Interoperability | **Code crosswalks**: ISO country/subdivision, UN/LOCODE ports, EIC area codes, ISO/RTO zones, HS ↔ SIEC. |
| **openFDA labels** (documents) | Searchable evidence | **Documents**: regulatory filings (FERC, SEC EDGAR), price-methodology documents, energy news, research reports. |
| **FAERS** (reported adverse events) | Safety signals | **Incidents and outages**: pipeline incidents (PHMSA), offshore incidents (BSEE), nuclear event reports (NRC), EU REMIT unavailability, refinery outages. *A reported outage is not a root-cause finding.* |
| **ClinicalTrials.gov** (research pipeline) | What is coming | **Project pipeline**: proposed / permitted / under-construction plants, pipelines, LNG trains; interconnection queues; FIDs. |
| **Drug similarity** (shared indications/reactions) | Analytics | **Asset similarity / peer groups**: shared fuel, technology, capacity band, vintage, market, outage pattern; crude-grade similarity by assay (API gravity, sulphur). |
| **GraphRAG** (vector → graph) | Semantic entry point | Same pattern: "LNG export terminals on the US Gulf with expansion plans" → nearest concepts and assets → graph traversal. |
| **Ontology (TTL) contract** | Schema governance | Same, extended with **units, as-of time, source and entitlement** annotations. |

New capabilities with no medical equivalent:

| Capability | Why energy needs it |
|---|---|
| **Time series** (prices, generation, flows, storage) | Most energy questions are about *values over time*. Series live in a time-series store; the graph holds the entities and a pointer to each series. |
| **Geospatial** | Basins, grid zones, shipping lanes, pipeline routes, proximity ("assets within 50 km of the hub"). |
| **Physical network topology** | Supply chains are directed networks (field → gathering → pipeline → terminal → vessel → port → refinery), so path queries are core functionality. |
| **Entitlements** | S&P data is licensed per product and per client. Every tool result must be filtered by what the user is entitled to see. |
| **Units and conversions** | bbl, t, MMBtu, MWh, Bcf/d, $/bbl vs $/t. Deterministic conversion only; the LLM never does arithmetic. |
| **Benchmark / PRA compliance** | Price assessments follow published methodologies under IOSCO principles for price reporting agencies. The assistant must never generate, imply or "estimate" an assessment. |

---

## 3. Architecture

```
 Sources                          Ingestion                      Stores                         Access
 ───────                          ─────────                      ──────                         ──────
 S&P internal datasets ─┐   batch ETL + streaming        ┌─ Neo4j  knowledge graph     ┌─ REST API (OIDC/SSO, entitlements)
 Public energy data  ───┼─► (Kafka / scheduled jobs) ───►├─ Time-series store (prices, │  ├─ Analyst dashboard
 Filings & news      ───┤   validation vs ontology       │   generation, flows)        ├─ LangGraph assistant (tools)
 Reference codes     ───┘   entity resolution (LEI, IDs) ├─ Vector indexes (Neo4j)     └─ Evals + audit + metrics
                            unit normalisation           └─ Document store (filings,
                            as-of / bitemporal stamping       methodology, news)
                                    ▲
                                    └── ontology/energy_graph.ttl   (schema + units + entitlement contract)
```

| Layer | Choice | Reason |
|---|---|---|
| Graph | Neo4j (Enterprise for RBAC and clustering) | Ownership chains, supply paths and similarity are graph-native. Same stack as MedGraph. |
| Time series | TimescaleDB, kdb+ or an existing internal series platform | Prices and flows are high-volume and need range and aggregate queries. The graph stores `(:Series {series_id})` pointers only. |
| Geospatial | Neo4j `point` + PostGIS for heavy spatial joins | Proximity and region membership. |
| Embeddings | Self-hosted embedding model (e.g. nomic-embed-text or an approved enterprise model) | Licensed text never leaves the controlled environment. |
| LLM | Approved enterprise LLM gateway with tool calling; zero data retention | Proprietary data and client questions stay contractually protected. |
| Orchestration | LangGraph ReAct agent with deterministic tools | Same pattern as MedGraph. |
| Identity | OIDC / SSO with entitlement claims | Per-user licensing, not shared API keys. |

---

## 4. Ontology (draft)

`ontology/energy_graph.ttl` would be the structural contract, validated in CI exactly as in MedGraph
(every label and relationship declared, domains/ranges checked, constraints generated from keys).
Two annotations are added to every class: `eg:sourceDataset` (lineage / entitlement) and
`eg:asOf` handling (valid-time vs record-time).

### Node classes (~20)

| Class | Key | Notes |
|---|---|---|
| `Company` | `lei` or internal company id | Legal entity; parent/subsidiary chain |
| `CompanyAlias` | `name` | Brands, former names, tickers. Never a company in its own right |
| `Asset` | `asset_id` | Abstract; specialised below |
| `PowerPlant`, `Refinery`, `LNGTerminal`, `Pipeline`, `Field`, `StorageSite`, `Port`, `Mine` | `asset_id` | Capacity, status, coordinates, commissioning year |
| `Unit` | `unit_id` | Generator / train / processing unit within an asset |
| `Commodity` | `code` | Taxonomy node (multi-parent), e.g. crude → light sweet |
| `Grade` | `code` | Specific traded grade with assay properties |
| `PriceSeries` | `series_id` | Pointer to the time-series store, with methodology id and unit |
| `Hub` / `Market` | `code` | Henry Hub, TTF, NBP, ISO zones, bidding zones |
| `Region` | `code` | Country, subdivision, basin, grid zone (multi-parent) |
| `Project` | `project_id` | Proposed / permitted / FID / construction / cancelled |
| `Incident` | `incident_id` | Outage, release, safety event; type, severity, start/end |
| `Regulation` | `id` | Policies, sanctions programmes, emissions schemes |
| `Document` | `doc_id` | Filing, methodology, news, research; with entitlement tag |
| `Technology` | `code` | CCGT, onshore wind, delayed coker, … (strict tree) |
| `Vessel` | `imo` | For trade-flow tracing (if licensed) |
| `EmissionsRecord` | `id` | Facility-level reported emissions |

### Relationship types (~25, selected)

| Relationship | Domain → Range |
|---|---|
| `SUBSIDIARY_OF` | Company → Company |
| `ALIAS_OF` | CompanyAlias → Company |
| `OWNS {share, as_of}` | Company → Asset |
| `OPERATES` | Company → Asset |
| `PART_OF` | Unit → Asset |
| `LOCATED_IN` | Asset → Region |
| `REGION_PARENT` | Region → Region |
| `USES_TECHNOLOGY` | Asset/Unit → Technology |
| `PRODUCES` / `CONSUMES` / `HANDLES` | Asset → Commodity/Grade |
| `CONNECTS_TO {capacity}` | Asset → Asset (physical network) |
| `DELIVERS_TO` | Asset → Hub/Market |
| `COMMODITY_PARENT` | Commodity → Commodity |
| `GRADE_OF` | Grade → Commodity |
| `PRICED_BY` | Grade/Hub → PriceSeries |
| `HAD_INCIDENT` | Asset/Unit → Incident |
| `PROPOSES` / `DEVELOPS` | Company → Project |
| `WOULD_CREATE` | Project → Asset |
| `SUBJECT_TO` | Company/Asset → Regulation |
| `MENTIONS` | Document → any entity |
| `REPORTED` | Asset → EmissionsRecord |
| `SIMILAR_TO {score, components}` | Asset → Asset (computed) |

Every fact-bearing relationship carries `source`, `as_of` and `confidence`. Ownership, capacity and status
change over time, and the assistant must answer "as of" a date.

---

## 5. Data sources

### 5.1 S&P Global Energy internal datasets *(S&P internal — confirm names, scope, licensing)*

| Domain | Candidate dataset | Role in the graph |
|---|---|---|
| Prices | Platts price assessments, symbols and methodology / specification guides | `PriceSeries`, `Grade`, `Hub`, methodology documents. The authoritative benchmark layer |
| News | Platts real-time news and commentary | `Document`, linked to entities by NER + resolution |
| Power assets | World Electric Power Plants database (WEPP) and related power data | `PowerPlant`, `Unit`, ownership, technology |
| Oil & refining | Refinery, oil infrastructure and crude assay datasets | `Refinery`, `Grade` assays, configuration, capacity |
| Gas & LNG | LNG, pipeline and gas infrastructure datasets | `LNGTerminal`, `Pipeline`, `CONNECTS_TO` |
| Upstream | Upstream E&P well, field and basin data | `Field`, `Region` (basins), operators |
| Trade flows | Vessel-tracking / commodity-flow datasets | `Vessel`, flows between `Port` and `Asset` |
| Companies | Company financials and corporate hierarchy (e.g. Capital IQ Pro) | `Company`, `SUBSIDIARY_OF`, identifiers |
| Sustainability | Emissions and ESG datasets (e.g. S&P Global Sustainable1 / Trucost) | `EmissionsRecord`, transition metrics |
| Research | Energy research, outlooks and scenarios | `Document` (scenario-labelled, never presented as fact) |

### 5.2 Public and open data

| Source | Content | Licence / access | Analogue in MedGraph |
|---|---|---|---|
| **US EIA Open Data API** (incl. EIA-860/923/930) | US plants, generators, fuel use, prices, grid operations | Public domain, free API key | openFDA |
| **Energy Institute Statistical Review of World Energy** | Country-level production, consumption, reserves | Free download, attribution | reference statistics |
| **Ember** | Global electricity generation and emissions | CC BY 4.0 | — |
| **ENTSO-E Transparency Platform** | EU generation, load, cross-border flows, unavailability (REMIT) | Free with registration | FAERS (outages) |
| **ENTSOG Transparency / GIE AGSI+ & ALSI** | EU gas flows, storage and LNG terminal inventories | Free | — |
| **Global Energy Monitor trackers** | Coal, gas, oil, LNG, pipeline, steel projects and units with status | CC BY 4.0 | ClinicalTrials.gov (pipeline) |
| **WRI Global Power Plant Database** | ~35k plants worldwide | CC BY 4.0, no longer updated | seed / cross-check only |
| **LBNL "Queued Up"** and ISO/RTO interconnection queues | Proposed generation and storage in US queues | Public | ClinicalTrials.gov |
| **PHMSA** pipeline incident and annual report data | US pipeline incidents, operators, mileage | Public | FAERS |
| **BSEE / BOEM** | Offshore incidents, platforms, leases | Public | FAERS |
| **US NRC event notification reports** | Nuclear plant events | Public | FAERS |
| **US EPA** GHGRP (FLIGHT), eGRID, CAMPD | Facility emissions, plant-level grid data | Public | — |
| **EU ETS registry (EUTL)** | Installation-level verified emissions | Public | — |
| **FERC eLibrary** | Pipeline and LNG certificates, tariffs, filings | Public | openFDA labels (documents) |
| **SEC EDGAR** full-text search and XBRL APIs | Company filings, subsidiaries (Ex. 21), financial facts | Public | documents |
| **GLEIF LEI** (+ level-2 parent data) | Legal entities and ownership | CC0 | RxNorm (identity) |
| **Wikidata** | Cross-identifiers, aliases, coordinates | CC0 | synonyms |
| **Open Energy Ontology (OEO)** | Domain ontology for energy-system concepts | Open licence | MeSH |
| **UN SIEC** / HS nomenclature / UN Comtrade | Energy-product classes, trade codes, trade flows | Public; Comtrade API limits | ATC / ICD crosswalks |
| **JODI Oil and Gas** | Monthly country oil and gas balances | Free | statistics |
| **IRENA statistics** | Renewable capacity and generation | Free, attribution | statistics |
| **NREL** (ATB, data catalogue) | Technology cost and performance benchmarks | Public | reference |
| **OpenStreetMap** (power / pipeline tags) | Geometry of lines, substations, pipelines | ODbL (share-alike — keep isolated) | — |
| **Climate TRACE** | Asset-level emissions estimates | CC BY 4.0 | — |
| **OFAC SDN, EU and UK sanctions lists** | Sanctioned entities and vessels | Public | — |
| **UN/LOCODE** | Port and location codes | Public | ICD-10 (codes) |
| **IEA data** | World energy balances, outlooks | Mostly licensed; some free | statistics (licence check) |

Each source gets an entry in a **source registry** (owner, licence, refresh cadence, entitlement tag,
attribution text). ETL refuses to load a dataset with no registry entry.

---

## 6. Assistant tools

Same design rules as MedGraph: deterministic, typed, read-only, entitlement-filtered, results wrapped as
untrusted data, and every value returned with `source`, `as_of` and `unit`.

| Tool | MedGraph analogue | What it does |
|---|---|---|
| `asset_overview(asset)` | `disease_overview` | Identity, owner/operator chain, location, capacity, technology, status, recent incidents, related projects, linked price series |
| `taxonomy_expand(term)` | `mesh_expand` | Expand a commodity/technology/region term through the hierarchy + synonyms ("LNG" → trains, terminals, FSRUs) |
| `search_documents(term, expand)` | `search_documents` | Graph-expanded search across filings, methodology and news (entitlement-filtered) |
| `incident_history(asset_or_company, period)` | `drug_safety` | Outages and incidents with type, duration and capacity offline. *Reported, not root cause* |
| `project_pipeline(region / commodity / company, status)` | `trials_pipeline` | Proposed → permitted → FID → construction → operating, with capacity and timeline |
| `decode_classification(code_or_entity)` | `decode_atc` | NAICS/GICS/HS/SIEC/technology path for a code or entity |
| `similar_assets(asset)` | `similar_drugs` | Peer group with an explained score (shared fuel, technology, size band, vintage, market, outage pattern) |
| `semantic_search(text)` | `semantic_search` | Vector entry point + graph context |
| `graph_qa(question)` | `graph_qa` | Guarded, read-only, ontology-validated Cypher (last resort) |
| `ownership_chain(company_or_asset)` | *(new)* | Ultimate parent, subsidiaries, equity shares, as-of date |
| `supply_path(from, to, commodity)` | *(new)* | Physical paths through the network (pipelines, terminals, ports) with capacities |
| `price_series(symbol_or_grade, start, end, agg)` | *(new)* | Retrieve **published** assessments only, with methodology reference. No forecasting, interpolation or "estimates" |
| `convert_units(value, from, to, commodity)` | *(new)* | Deterministic conversion (density and heat-content aware). The model never does arithmetic itself |

Deliberately **not** offered: price prediction, trading recommendations, generated "assessments",
and any write action.

### Example walkthrough (illustrative)

*"What does EnergyGraph know about Sabine Pass?"*

1. **Resolve identity.** "Sabine Pass" is ambiguous (LNG terminal, a pass and a nearby area). Resolution
   picks `LNGTerminal: Sabine Pass LNG` and lists the alternatives.
2. **Ownership chain.** Operator → parent company → ultimate parent, with equity shares and as-of date.
3. **Classification.** LNG export terminal → liquefaction → technology of each train.
4. **Network.** Pipelines that feed the terminal (`CONNECTS_TO`), upstream supply basins, delivery markets.
5. **Incidents.** Reported outages and incidents for the terminal and its trains in a period.
6. **Projects.** Expansion projects (`WOULD_CREATE`) and their status.
7. **Markets.** Linked price series (e.g. US Gulf Coast gas hub and destination LNG benchmarks), returned
   as published assessments with methodology references.
8. **Peers.** Similar export terminals by capacity, technology and vintage, with the score explained.

Each step is one deterministic tool call. Every number in the answer carries a source and an as-of date.

---

## 7. What is reused from MedGraph unchanged

| Component | Reuse |
|---|---|
| Ontology-as-contract + validator + CI tests | As is (new TTL) |
| ETL framework: cached HTTP, retries, idempotent `MERGE`, batch writes | As is |
| Entity resolution pattern (exact → alias → fuzzy → vector) | As is |
| Hierarchy expansion (multi-parent) | As is (taxonomy, regions) |
| Cypher-only similarity with explainable components | Extended scoring |
| GraphRAG (vector entry → graph context) | As is |
| LangGraph agent, tool budget, deadline, recursion limit | As is |
| Grounding check (identifier verification) | Extended to asset ids, price symbols, LEIs, project ids, and to **numbers** (every figure must appear in a tool result) |
| Untrusted-data envelope, injection neutralisation | As is (news text is a larger injection surface than drug labels) |
| Read-only DB client, guarded `graph_qa` | As is |
| Rate limits, body limits, sanitisation, security headers, audit logs, metrics | As is |
| Evals harness | New golden cases |

Changed for an enterprise, licensed-data setting:

| Area | MedGraph | EnergyGraph |
|---|---|---|
| Authentication | Hashed API keys + scopes | **OIDC / SSO** (JWT) with user, client and entitlement claims; service keys only for system-to-system use |
| Authorisation | Scope per route | Scope per route **and data-level entitlement filtering in every tool and query** (dataset, region, product tier) |
| Data freshness | Static snapshots | Streaming + scheduled refresh; every answer states the as-of date; stale-data warnings |
| Storage | Neo4j only | Neo4j + time-series + document store |
| Compliance | Medical disclaimer, crisis handling | PRA/benchmark rules, MNPI controls, sanctions sensitivity, attribution and redistribution limits |

---

## 8. Governance, security and compliance

| Risk | Control |
|---|---|
| **Entitlement leakage** (a user sees data they haven't licensed) | Entitlement claims from SSO are passed into every tool. Queries filter on a per-node/relationship `entitlement` tag. The vector search post-filters by entitlement, and **retrieval happens only after filtering**. Tests prove that a restricted user cannot get a restricted value through any tool, including `graph_qa`. |
| **Benchmark integrity** | `price_series` returns published values only, with a methodology link. The system prompt and output checks forbid "estimated assessment" language. Price figures in answers are verified against tool output. |
| **Hallucinated numbers** | Numeric grounding: each number in an answer must match a tool-returned value (after unit normalisation) or be flagged and removed. Arithmetic is done only by `convert_units` or aggregation tools. |
| **MNPI / market-sensitive information** | Unpublished or embargoed content is excluded from the graph until its release time (record-time vs valid-time). Audit logs record which datasets each answer touched. |
| **Sanctions sensitivity** | Sanctions designations are shown with source and date. The assistant states facts, not legal conclusions. |
| **Prompt injection via news/filings** | Untrusted-data envelope, neutralisation, read-only tools, and no tool that can send or write. |
| **Redistribution / attribution** | Source registry attribution text is attached to results. Share-alike data (e.g. ODbL) is kept in isolated layers. |
| **Client confidentiality** | Questions are logged as fingerprints by default. LLM gateway with zero retention and no training on client prompts. Regional data residency where contracts require it. |
| **Model risk** | Evals gate every model/prompt/tool change. Human-review queue for flagged answers. Model and prompt versions recorded per answer. |

---

## 9. Delivery plan

| Phase | Scope | Outcome |
|---|---|---|
| **0 — Foundations (4–6 weeks)** | Ontology v1, source registry, entitlement model, platform reuse from MedGraph, public data only (EIA, GEM, GLEIF, PHMSA, ENTSO-E) | Working graph and assistant on open data; security and evals in place |
| **1 — Power & gas MVP (8–10 weeks)** | Internal power-plant and gas/LNG datasets, ownership chains, incidents, project pipeline, `asset_overview`, `ownership_chain`, `similar_assets` | Analyst pilot with 10–20 users; entitlement tests passing |
| **2 — Prices & documents (8 weeks)** | Price-series pointers + methodology, news/filings GraphRAG, numeric grounding, `price_series`, `convert_units` | Questions combining "asset + market + news" answered with citations |
| **3 — Oil, refining & flows (8–10 weeks)** | Refineries, crude assays, `supply_path`, vessel/trade flows (if licensed) | Supply-chain and crude-slate questions |
| **4 — Scale & integrate** | Clustering, streaming refresh, SSO integration into client platforms, API productisation | General availability |

### Success measures
* **Groundedness:** ≥ 99% of identifiers and numbers in answers verifiable against tool output (measured by evals and production checks).
* **Entitlement safety:** zero restricted-data exposures in automated adversarial tests.
* **Coverage:** share of analyst pilot questions answered without "not in the graph".
* **Recall gain from graph expansion:** expanded vs keyword search on a labelled document set (the MedGraph metric, reused).
* **Analyst time saved** on recurring research tasks (pilot survey + task timing).
* **Latency:** p95 under 10 s for single-entity questions.

---

## 10. Open questions for S&P Global Energy

1. Which internal datasets are in scope for the MVP, and what are their entitlement models (per dataset, per region, per tier)?
2. Is there an existing enterprise entity master (company / asset IDs) that the graph should adopt rather than re-create?
3. Which enterprise LLM gateway and hosting regions are approved for licensed content?
4. Should price series be served from an existing internal time-series platform rather than a new store?
5. Which compliance teams (benchmark governance, legal, data licensing) must approve the assistant's answer policy?
6. Is the first user group internal analysts, external clients, or both? This changes authentication, attribution and logging requirements.
