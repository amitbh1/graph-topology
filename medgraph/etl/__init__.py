"""ETL pipeline. Steps run in dependency order; each is idempotent (MERGE-based)."""

from __future__ import annotations

from typing import Callable

from ..db import Neo4jClient


def _steps() -> dict[str, Callable[[Neo4jClient], None]]:
    from ..schema import create_schema
    from .atc import link_drugs_to_atc, load_atc
    from .clinical_trials import load_trials
    from .embed import embed_all
    from .faers import load_faers
    from .icd10 import load_icd10
    from .mesh import load_mesh
    from .openfda_labels import load_labels
    from .rxnorm import load_brand_aliases
    from .similarity import compute_similarity

    return {
        "schema": create_schema,
        "atc": load_atc,
        "mesh": load_mesh,
        "labels": load_labels,
        "rxnorm": load_brand_aliases,
        "atc-link": link_drugs_to_atc,
        "faers": load_faers,
        "trials": load_trials,
        "icd10": load_icd10,
        "similarity": compute_similarity,
        "embed": embed_all,
    }


STEP_NAMES = ["schema", "atc", "mesh", "labels", "rxnorm", "atc-link", "faers", "trials",
              "icd10", "similarity", "embed"]


def run(client: Neo4jClient, steps: list[str]) -> None:
    available = _steps()
    if steps == ["all"]:
        steps = STEP_NAMES
    for step in steps:
        if step not in available:
            raise SystemExit(f"unknown ETL step {step!r}; choose from {', '.join(STEP_NAMES)} or all")
        print(f"== {step}")
        available[step](client)
