"""Deterministic energy unit conversion. The assistant never does arithmetic itself.

Supports quantities, rates (`/d`, `/y`) and prices (`USD/<unit>`), within a
dimension (e.g. bbl -> m3) and across dimensions when a commodity-dependent
factor applies (e.g. crude t -> bbl via API gravity). Every non-exact factor is
reported in `assumptions`, so answers can state them.
"""

from __future__ import annotations

import re

# dimension -> unit -> factor to the dimension's base unit
UNITS: dict[str, dict[str, float]] = {
    "liquid_volume": {"bbl": 1.0, "kb": 1e3, "mb": 1e6, "m3": 1 / 0.158987294928, "gal": 1 / 42.0},
    "energy": {"mmbtu": 1.0, "btu": 1e-6, "therm": 0.1, "gj": 1 / 1.055056, "mwh": 1 / 0.29307107,
               "kwh": 1 / 293.07107, "gwh": 1e3 / 0.29307107, "twh": 1e6 / 0.29307107},
    "gas_volume": {"mcf": 1.0, "mmcf": 1e3, "bcf": 1e6, "tcf": 1e9, "bcm": 35.3146667e6, "mcm": 35.3146667e3},
    "mass": {"t": 1.0, "kt": 1e3, "mt": 1e6},
    "power": {"mw": 1.0, "gw": 1e3, "kw": 1e-3},
}
ALIASES = {"barrel": "bbl", "barrels": "bbl", "tonne": "t", "tonnes": "t", "mtpa": "mt/y", "b/d": "bbl/d",
           "kb/d": "kb/d", "bpd": "bbl/d", "mmscf": "mmcf", "cubic metre": "m3", "mtoe": "mtoe"}
RATE = {"d": 365.0, "y": 1.0}           # per-year multipliers

DEFAULTS = {"heat_content_mmbtu_per_mcf": 1.036,   # US average dry gas, EIA (varies ~1.01-1.09)
            "lng_mmbtu_per_tonne": 52.0,            # typical LNG (varies ~51-54)
            "lng_bcm_per_mt": 1.36,                 # regasified volume per tonne of LNG (varies)
            "crude_bbl_per_tonne": 7.33}            # world average crude (use api_gravity when known)


class UnitError(ValueError):
    pass


def _parse(unit: str) -> tuple[str | None, str, str | None]:
    """'USD/bbl' -> ('USD', 'bbl', None); 'kb/d' -> (None, 'kb', 'd')."""
    u = ALIASES.get(unit.strip().lower(), unit.strip().lower())
    currency = None
    m = re.fullmatch(r"([a-z]{3})/(.+)", u)
    if m and m.group(1) in ("usd", "eur", "gbp", "jpy", "cny"):
        currency, u = m.group(1).upper(), m.group(2)
        u = ALIASES.get(u, u)
    rate = None
    m = re.fullmatch(r"(.+)/(d|y)", u)
    if m:
        u, rate = m.group(1), m.group(2)
    return currency, u, rate


def _dimension(u: str) -> str:
    for dim, units in UNITS.items():
        if u in units:
            return dim
    raise UnitError(f"unknown unit {u!r}; supported: " + ", ".join(sorted({x for v in UNITS.values() for x in v})))


def _bbl_per_tonne(params: dict, assumptions: list[str]) -> float:
    if params.get("api_gravity") is not None:
        api = float(params["api_gravity"])
        if not 5 <= api <= 80:
            raise UnitError("api_gravity must be between 5 and 80")
        sg = 141.5 / (api + 131.5)
        factor = 1 / (sg * 0.999016 * 0.158987294928)
        assumptions.append(f"crude density from API {api:g} (SG {sg:.4f}) -> {factor:.3f} bbl/t")
        return factor
    factor = float(params.get("crude_bbl_per_tonne", DEFAULTS["crude_bbl_per_tonne"]))
    assumptions.append(f"assumed {factor} bbl per tonne of crude (pass api_gravity for a grade-specific factor)")
    return factor


def _cross(src: str, dst: str, commodity: str | None, params: dict, assumptions: list[str]) -> float:
    """Factor converting one base unit of `src` dimension into base units of `dst`."""
    c = (commodity or "").lower()
    pair = (src, dst)
    if pair in (("gas_volume", "energy"), ("energy", "gas_volume")):
        hc = float(params.get("heat_content_mmbtu_per_mcf", DEFAULTS["heat_content_mmbtu_per_mcf"]))
        assumptions.append(f"natural gas heat content {hc} MMBtu/Mcf")
        return hc if src == "gas_volume" else 1 / hc
    if pair in (("mass", "liquid_volume"), ("liquid_volume", "mass")):
        if c and "crude" not in c and "oil" not in c:
            raise UnitError("mass<->liquid volume is supported for crude oil (commodity='crude oil')")
        f = _bbl_per_tonne(params, assumptions)
        return f if src == "mass" else 1 / f
    if pair in (("mass", "energy"), ("energy", "mass")):
        if "lng" not in c and "liquefied" not in c:
            raise UnitError("mass<->energy is supported for LNG (commodity='lng')")
        f = float(params.get("lng_mmbtu_per_tonne", DEFAULTS["lng_mmbtu_per_tonne"]))
        assumptions.append(f"LNG energy content {f} MMBtu/t")
        return f if src == "mass" else 1 / f
    if pair in (("mass", "gas_volume"), ("gas_volume", "mass")):
        if "lng" not in c and "liquefied" not in c:
            raise UnitError("mass<->gas volume is supported for LNG (commodity='lng')")
        bcm_per_mt = float(params.get("lng_bcm_per_mt", DEFAULTS["lng_bcm_per_mt"]))
        assumptions.append(f"{bcm_per_mt} bcm of regasified gas per Mt of LNG")
        mcf_per_t = bcm_per_mt * UNITS["gas_volume"]["bcm"] / 1e6
        return mcf_per_t if src == "mass" else 1 / mcf_per_t
    raise UnitError(f"no conversion between {src} and {dst}")


def convert(value: float, from_unit: str, to_unit: str, commodity: str | None = None, **params) -> dict:
    if not isinstance(value, (int, float)) or value != value or abs(value) > 1e15:
        raise UnitError("value must be a finite number")
    fc, fu, fr = _parse(from_unit)
    tc, tu, tr = _parse(to_unit)
    if (fc is None) != (tc is None) or (fc and fc != tc):
        raise UnitError("prices convert only within one currency (FX conversion is not supported)")
    if (fr is None) != (tr is None) and fc is None:
        raise UnitError("cannot convert between a quantity and a rate")
    fd, td = _dimension(fu), _dimension(tu)
    assumptions: list[str] = []
    factor = UNITS[fd][fu]                                   # source unit -> source base
    if fd != td:
        factor *= _cross(fd, td, commodity, params, assumptions)
    factor /= UNITS[td][tu]                                  # target base -> target unit
    if fr and tr:
        factor *= RATE[fr] / RATE[tr]
    if fc:                                                   # prices are per unit: invert the quantity factor
        factor = 1 / factor
    result = value * factor
    return {"value": value, "from_unit": from_unit, "to_unit": to_unit, "result": float(f"{result:.6g}"),
            "factor": float(f"{factor:.8g}"), "assumptions": assumptions, "exact": not assumptions}
