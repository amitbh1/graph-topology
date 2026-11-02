"""EnergyGraph core reference data: concepts the public taxonomies do not cover,
and the mapping from source fuel names to taxonomy labels."""

from __future__ import annotations

from typing import Iterator

from ..base import SourceConnector, SourceMeta
from ..records import ConceptRecord

# Source fuel name (lower-case) -> taxonomy label (OEO where it exists, core otherwise).
FUEL_LABELS = {
    "coal": "coal", "gas": "natural gas", "oil": "crude oil", "hydro": "hydro energy",
    "nuclear": "nuclear fuel", "solar": "solar energy", "wind": "wind energy", "biomass": "biomass",
    "geothermal": "geothermal energy", "petcoke": "petroleum coke", "waste": "waste fuel",
    "storage": "energy storage", "cogeneration": "cogeneration", "wave and tidal": "marine energy",
    "other": "other fuel", "lng": "liquefied natural gas", "crude": "crude oil",
}

CORE_CONCEPTS = [
    ("eg:waste-fuel", "waste fuel", "Municipal or industrial waste used as fuel.", []),
    ("eg:energy-storage", "energy storage", "Technologies that store energy for later use (batteries, pumped hydro).", []),
    ("eg:cogeneration", "cogeneration", "Combined generation of electricity and useful heat.", []),
    ("eg:marine-energy", "marine energy", "Energy from waves and tides.", []),
    ("eg:other-fuel", "other fuel", "Fuel not classified elsewhere.", []),
    ("eg:refining", "oil refining", "Processing of crude oil into petroleum products.", []),
    ("eg:liquefaction", "lng liquefaction", "Cooling natural gas to liquid for export.", ["eg:lng-terminal-technology"]),
    ("eg:regasification", "lng regasification", "Returning LNG to gaseous state for import.", ["eg:lng-terminal-technology"]),
    ("eg:lng-terminal-technology", "lng terminal technology", "Liquefaction and regasification technologies.", []),
]


class CoreTaxonomy(SourceConnector):
    meta = SourceMeta(
        source_id="energygraph-core", title="EnergyGraph core reference concepts", kind="internal",
        license="Internal reference data", attribution="EnergyGraph", url="", refresh="with releases",
        record_kinds=("concept",),
    )

    def fetch(self, limit: int | None = None) -> Iterator[ConceptRecord]:
        for code, label, definition, parents in CORE_CONCEPTS:
            yield ConceptRecord(source=self.meta.source_id, code=code, label=label, definition=definition,
                                parents=parents)
