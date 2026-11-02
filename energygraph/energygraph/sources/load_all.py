"""Registers the built-in connectors."""

from __future__ import annotations

from .base import register


def register_builtin() -> None:
    from .organization.connector import DATASETS, OrganizationSource
    from .public.core import CoreTaxonomy
    from .public.eia import EiaPrices
    from .public.gleif import GleifResolver
    from .public.gppd import GlobalPowerPlantDatabase
    from .public.oeo import OpenEnergyOntology
    from .public.wikidata import WikidataAssets
    from .seed import SeedSource

    for connector in (CoreTaxonomy(), OpenEnergyOntology(), GlobalPowerPlantDatabase(), WikidataAssets(),
                      GleifResolver(), EiaPrices(), SeedSource()):
        register(connector)
    for dataset in DATASETS:
        register(OrganizationSource(dataset))
