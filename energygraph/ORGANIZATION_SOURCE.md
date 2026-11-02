# Integrating organization-source datasets

Licensed datasets from **organization-name** are not wired in yet. EnergyGraph ships the interface they
plug into, so integrating a dataset is configuration plus a payload mapping, not graph code.

```
organization feed ──► Transport (file | https) ──► OrganizationSource(dataset)
                         │                             │  validates every record against the canonical contract
                         │                             │  imposes source = org-<dataset>, entitlement = org:<dataset>
                         │                             └─ quarantines invalid records (data/quarantine/)
                         ▼
                      Loader ──► Neo4j (entitlement-tagged nodes & relationships) + time-series store
                                   │
                      every query / tool / API response filters on the caller's entitlements
```

Code: [energygraph/sources/organization/connector.py](energygraph/sources/organization/connector.py) ·
Contract: [energygraph/sources/records.py](energygraph/sources/records.py) ·
Synthetic examples: [energygraph/sources/organization/samples/](energygraph/sources/organization/samples/)

## 1. Datasets

| Dataset id | Content | Record kinds allowed | Entitlement |
|---|---|---|---|
| `companies` | Companies and corporate hierarchy | `company` | `org:companies` |
| `power-assets` | Power plants and units | `asset`, `company` | `org:power-assets` |
| `refinery-assets` | Refineries and capacities | `asset`, `company` | `org:refinery-assets` |
| `lng-assets` | LNG terminals and trains | `asset`, `company` | `org:lng-assets` |
| `pipelines` | Pipelines and network connections | `asset`, `connection`, `company` | `org:pipelines` |
| `upstream` | Fields and basins | `asset`, `company` | `org:upstream` |
| `outages` | Outages and incidents | `incident` | `org:outages` |
| `projects` | Project pipeline | `project`, `company` | `org:projects` |
| `price-series` | Series definitions and methodology links | `price_series` | `org:price-series` |
| `prices` | Published price observations | `price` | `org:prices` |
| `news` | News and commentary | `document` | `org:news` |
| `taxonomy` | Commodity and grade taxonomy | `concept` | `org:taxonomy` |

To add a dataset, add one line to `DATASETS` in `connector.py`. Nothing else changes.

## 2. Record contract

One JSON object per record, with a `kind` discriminator. Every field is validated: unknown fields are rejected,
along with wrong types, out-of-range values, bad id formats and illegal enum values.

* Identifiers use the form `org:<local id>` (`^[a-z][a-z0-9-]*:[A-Za-z0-9._/-]{1,120}$`). Records may reference
  public ids such as `gppd:USA0001234`, `wd:Q123` or `lei:<LEI>` to attach licensed facts to public assets and companies.
* Countries are ISO 3166 alpha-3 codes. Dates are ISO `YYYY-MM-DD`. Capacity units are one of
  `MW, MWh, bbl/d, kb/d, mtpa, bcm/y, MMcf/d, MMBtu/d, km`. Price units look like `USD/bbl` or `USD/MMBtu`.
* **Do not send `source` or `entitlement`.** The connector sets them. A payload that tries to set
  `entitlement: public` is quarantined.

```json
{"kind": "asset", "asset_id": "org:PP-0001", "asset_type": "PowerPlant", "name": "Kestrel Bay Power Station",
 "country": "USA", "latitude": 29.9, "longitude": -93.7, "capacity": 780.0, "capacity_unit": "MW",
 "status": "operating", "commissioning_year": 2013, "concepts": ["natural gas"],
 "owners": [{"company_id": "org:CO-1001", "share": 1.0}], "operator_id": "org:CO-1001", "as_of": "2026-01-01"}
{"kind": "incident", "incident_id": "org:OUT-5001", "asset_id": "gppd:USA0001234", "incident_type": "unplanned_outage",
 "start": "2025-12-02", "end": "2025-12-05", "capacity_offline": 390.0, "capacity_unit": "MW", "as_of": "2026-01-01"}
```

Field lists for every kind are in `records.py`: `company`, `asset`, `connection`, `incident`, `project`,
`price_series`, `price`, `document`, `concept`.

## 3. Transports

### File drop (batch deliveries, contract testing)
```
ORG_SOURCE_MODE=file
ORG_SOURCE_DIR=/secure/drops/organization          # contains <dataset>.jsonl files
```

### HTTPS API
```
ORG_SOURCE_MODE=http
ORG_SOURCE_BASE_URL=https://data.organization.example   # https is mandatory
ORG_SOURCE_TOKEN=<from your secret store>
ORG_SOURCE_TIMEOUT_S=30
```
The connector expects, for each dataset:
```
GET {base}/v1/datasets/{dataset}/records?limit=500&cursor=<opaque>
Authorization: Bearer <token>
200 -> {"records": [...], "next_cursor": "<opaque>" | null}
```
Built in: retries with back-off on 429/502/503/504 (honours `Retry-After`), a 20 MiB page limit, a
10,000-page limit, fail-fast on 401/403, and error messages reduced to the exception class so URLs and
tokens never reach a log.

If the organization's API has a different shape, implement the `Transport` protocol (`pages()` and
`check()`). That keeps the mapping in one class, and validation, quarantine and entitlements stay the same.

## 4. Workflow

```bash
python -m energygraph org validate /path/to/sample-drop/          # contract check, no database writes
python -m energygraph org status                                  # transport reachable / files present
python -m energygraph org ingest power-assets outages             # load selected datasets
python -m energygraph etl similarity embed                        # refresh derived data
python -m energygraph validate                                    # ontology + entitlement checks on the DB
```

Grant access by issuing credentials with the matching entitlements:
```bash
python -m energygraph apikey analyst --scopes read,chat --entitlements public,org:power-assets,org:outages
```
For SSO users, put the entitlements in the token claim named by `OIDC_ENTITLEMENT_CLAIM`.

## 5. Guarantees (tested in `tests/test_entitlements.py`)

* A caller without `org:<dataset>` never sees that dataset's nodes, relationships, prices or documents,
  through any of the 14 tools or any API endpoint. This includes semantic search, ownership chains,
  supply paths and similarity.
* A licensed record never overwrites a public node's properties. Licensed facts about public assets become
  separate, entitlement-tagged relationships and nodes.
* Similarity scores never combine assets with different entitlements.
* `graph_qa` (LLM-generated Cypher) runs only for principals entitled to all data (`*`).
* Licensed news text passes through injection neutralisation and is labelled as untrusted data for the model.
