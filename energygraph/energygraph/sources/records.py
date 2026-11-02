"""Canonical record contract.

Every connector — public or organization — emits these validated records and
nothing else. The loader only understands this contract, so a new source
(including the organization sources integrated later) needs no graph code:
it maps its payload to these models.

Identifiers are namespaced `<namespace>:<local id>`, e.g. `gppd:USA0001234`,
`wd:Q123`, `lei:5493001KJTIIGC8Y1R12`, `org:PP-000123`.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Id = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*:[A-Za-z0-9._/-]{1,120}$")]
Name = Annotated[str, Field(min_length=1, max_length=300)]
Country = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
Entitlement = Annotated[str, Field(pattern=r"^(public|org:[a-z0-9-]{1,40})$")]

ASSET_TYPES = ("PowerPlant", "Refinery", "LNGTerminal", "Pipeline", "Field", "StorageSite")
AssetType = Literal["PowerPlant", "Refinery", "LNGTerminal", "Pipeline", "Field", "StorageSite"]
AssetStatus = Literal["operating", "construction", "planned", "mothballed", "retired", "cancelled", "unknown"]
ProjectStatus = Literal["announced", "permitting", "fid", "construction", "commissioning", "operating",
                        "suspended", "cancelled"]
IncidentType = Literal["planned_outage", "unplanned_outage", "forced_outage", "safety", "environmental", "other"]
CapacityUnit = Literal["MW", "MWh", "bbl/d", "kb/d", "mtpa", "bcm/y", "MMcf/d", "MMBtu/d", "km"]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)
    source: Annotated[str, Field(pattern=r"^[a-z0-9-]{2,40}$")]
    entitlement: Entitlement = "public"
    as_of: Optional[dt.date] = None


class ConceptRecord(Record):
    kind: Literal["concept"] = "concept"
    code: Id
    label: Name
    definition: Optional[Annotated[str, Field(max_length=2000)]] = None
    parents: list[Id] = Field(default_factory=list, max_length=50)
    synonyms: list[Name] = Field(default_factory=list, max_length=100)


class OwnerRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    company_id: Id
    share: Optional[Annotated[float, Field(ge=0, le=1)]] = None


class CompanyRecord(Record):
    kind: Literal["company"] = "company"
    company_id: Id
    name: Name
    lei: Optional[Annotated[str, Field(pattern=r"^[A-Z0-9]{18}[0-9]{2}$")]] = None
    country: Optional[Country] = None
    aliases: list[Name] = Field(default_factory=list, max_length=50)
    parent_company_id: Optional[Id] = None


class AssetRecord(Record):
    kind: Literal["asset"] = "asset"
    asset_id: Id
    asset_type: AssetType
    name: Name
    aliases: list[Name] = Field(default_factory=list, max_length=50)
    country: Optional[Country] = None
    country_name: Optional[Name] = None
    latitude: Optional[Annotated[float, Field(ge=-90, le=90)]] = None
    longitude: Optional[Annotated[float, Field(ge=-180, le=180)]] = None
    capacity: Optional[Annotated[float, Field(ge=0, le=1e7)]] = None
    capacity_unit: Optional[CapacityUnit] = None
    status: AssetStatus = "unknown"
    commissioning_year: Optional[Annotated[int, Field(ge=1850, le=2100)]] = None
    concepts: list[Name] = Field(default_factory=list, max_length=20, description="fuel/technology labels or codes")
    owners: list[OwnerRef] = Field(default_factory=list, max_length=50)
    operator_id: Optional[Id] = None

    @model_validator(mode="after")
    def _capacity_needs_unit(self):
        if self.capacity is not None and self.capacity_unit is None:
            raise ValueError("capacity requires capacity_unit")
        return self


class ConnectionRecord(Record):
    kind: Literal["connection"] = "connection"
    from_asset_id: Id
    to_asset_id: Id
    capacity: Optional[Annotated[float, Field(ge=0)]] = None
    capacity_unit: Optional[CapacityUnit] = None


class IncidentRecord(Record):
    kind: Literal["incident"] = "incident"
    incident_id: Id
    asset_id: Id
    incident_type: IncidentType
    start: dt.date
    end: Optional[dt.date] = None
    capacity_offline: Optional[Annotated[float, Field(ge=0)]] = None
    capacity_unit: Optional[CapacityUnit] = None
    description: Optional[Annotated[str, Field(max_length=2000)]] = None

    @model_validator(mode="after")
    def _dates(self):
        if self.end and self.end < self.start:
            raise ValueError("end before start")
        return self


class ProjectRecord(Record):
    kind: Literal["project"] = "project"
    project_id: Id
    name: Name
    asset_type: AssetType
    status: ProjectStatus
    capacity: Optional[Annotated[float, Field(ge=0, le=1e7)]] = None
    capacity_unit: Optional[CapacityUnit] = None
    country: Optional[Country] = None
    country_name: Optional[Name] = None
    developer_ids: list[Id] = Field(default_factory=list, max_length=20)
    expands_asset_id: Optional[Id] = None
    expected_year: Optional[Annotated[int, Field(ge=2000, le=2100)]] = None
    concepts: list[Name] = Field(default_factory=list, max_length=20)


class PriceSeriesRecord(Record):
    kind: Literal["price_series"] = "price_series"
    series_id: Id
    name: Name
    unit: Annotated[str, Field(pattern=r"^[A-Z]{3}/[A-Za-z0-9]{1,10}$")]  # e.g. USD/bbl, USD/MMBtu
    frequency: Literal["daily", "weekly", "monthly", "annual"]
    commodity: Name
    hub_code: Optional[Id] = None
    hub_name: Optional[Name] = None
    hub_country: Optional[Country] = None
    methodology_url: Optional[Annotated[str, Field(pattern=r"^https://", max_length=500)]] = None


class PriceRecord(Record):
    kind: Literal["price"] = "price"
    series_id: Id
    date: dt.date
    value: Annotated[float, Field(ge=-1e6, le=1e6)]


class DocumentRecord(Record):
    kind: Literal["document"] = "document"
    doc_id: Id
    title: Name
    text: Annotated[str, Field(max_length=20000)]
    published_at: Optional[dt.date] = None
    url: Optional[Annotated[str, Field(pattern=r"^https?://", max_length=500)]] = None
    asset_ids: list[Id] = Field(default_factory=list, max_length=50)
    company_ids: list[Id] = Field(default_factory=list, max_length=50)

    @field_validator("text")
    @classmethod
    def _no_nul(cls, v: str) -> str:
        return v.replace("\x00", "")


AnyRecord = Annotated[Union[ConceptRecord, CompanyRecord, AssetRecord, ConnectionRecord, IncidentRecord,
                            ProjectRecord, PriceSeriesRecord, PriceRecord, DocumentRecord],
                      Field(discriminator="kind")]
RECORD_KINDS = ("concept", "company", "asset", "connection", "incident", "project", "price_series", "price",
                "document")
