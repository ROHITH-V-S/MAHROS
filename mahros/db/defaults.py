"""Real reference data and the default live state, as database rows.

Everything here is computed from the files vendored in
`mahros/calibration/data/`, so loading the database needs no network access
and gives the same rows every time.

The *default* live state is what "Restore defaults" returns to: each hospital's
beds and occupancy as it reported them to HHS in the reference week. Every
value carries a provenance label, because some pools (step-down beds, OR
slots, ventilators) are not in any public dataset and are assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..calibration.capability import load_capability
from ..calibration.facilities import load_vendor
from ..calibration.routing import load_routes
from ..core.types import ResourceType
from ..sim.real_network import _nearest_reported, build_real_network

HHS = "HHS/CDC NHSN facility report"
POS = "CMS Provider of Services"


@dataclass
class ReferenceData:
    metro: str
    week: str
    source: dict[str, Any]
    hospitals: list[dict[str, Any]]
    capabilities: list[dict[str, Any]]
    weekly_reports: list[dict[str, Any]]
    travel_times: list[dict[str, Any]]
    resources: list[dict[str, Any]]


def _capacity_provenance(kind: ResourceType, from_pos: bool) -> tuple[str, str]:
    return {
        ResourceType.ICU_BED: ("observed", f"{HHS}: total_staffed_adult_icu_beds"),
        ResourceType.WARD_BED: ("derived", f"{HHS}: inpatient beds minus ICU and step-down"),
        ResourceType.HDU_BED: ("assumed", "0.5 x ICU beds; not in any public dataset"),
        ResourceType.OR_SLOT: ("assumed", "1 per 30 inpatient beds; not reported"),
        ResourceType.VENTILATOR: ("assumed", "0.8 x ICU beds; not reported"),
        ResourceType.CATH_LAB: (
            ("observed", f"{POS}: cardiac catheterisation room count") if from_pos
            else ("assumed", "tier guess; no POS record")),
    }[kind]


def default_occupancy(caps: dict[ResourceType, int], used: float | None,
                      icu_used: float | None, beds: float,
                      ) -> dict[ResourceType, tuple[int, str, str]]:
    """Occupancy per pool in the reference week: (occupied, provenance, source).

    ICU is reported directly. Ward and step-down share what is left of the
    inpatient count, split by size. Nothing public reports OR, ventilator or
    cath-lab use, so those follow the hospital's reported occupancy rate and
    are labelled assumed.
    """
    inpatient_rate = min(1.0, (used / beds)) if (used is not None and beds) else 0.70
    icu_cap = caps.get(ResourceType.ICU_BED, 0)
    out: dict[ResourceType, tuple[int, str, str]] = {}

    if icu_used is not None and icu_cap:
        icu_occ = min(icu_cap, max(0, round(icu_used)))
        icu_rate = icu_occ / icu_cap
        out[ResourceType.ICU_BED] = (icu_occ, "observed", f"{HHS}: staffed adult ICU beds occupied")
    else:
        icu_occ = round(icu_cap * inpatient_rate)
        icu_rate = inpatient_rate
        out[ResourceType.ICU_BED] = (icu_occ, "derived",
                                     f"ICU not reported; {HHS} inpatient occupancy rate applied")

    ward_cap = caps.get(ResourceType.WARD_BED, 0)
    hdu_cap = caps.get(ResourceType.HDU_BED, 0)
    if used is not None:
        rest = max(0.0, used - (icu_used if icu_used is not None else icu_occ))
        share = ward_cap / (ward_cap + hdu_cap) if (ward_cap + hdu_cap) else 1.0
        ward_occ = min(ward_cap, round(rest * share))
        hdu_occ = min(hdu_cap, round(rest * (1 - share)))
        src = f"{HHS}: inpatient beds occupied minus ICU, split by pool size"
        out[ResourceType.WARD_BED] = (ward_occ, "derived", src)
        if hdu_cap:
            out[ResourceType.HDU_BED] = (hdu_occ, "derived", src)
    else:
        out[ResourceType.WARD_BED] = (round(ward_cap * 0.70), "assumed",
                                      "occupancy not reported; network mean 0.70")
        if hdu_cap:
            out[ResourceType.HDU_BED] = (round(hdu_cap * 0.70), "assumed",
                                         "occupancy not reported; network mean 0.70")

    for kind, rate, label in (
        (ResourceType.OR_SLOT, inpatient_rate, "inpatient"),
        (ResourceType.CATH_LAB, inpatient_rate, "inpatient"),
        (ResourceType.VENTILATOR, icu_rate, "ICU"),
    ):
        cap = caps.get(kind, 0)
        if cap:
            out[kind] = (min(cap, round(cap * rate)), "assumed",
                         f"no public data on use; set to the hospital's {label} occupancy rate")
    return out


def build_reference(metro: str = "houston", week: str | None = None) -> ReferenceData:
    """Every row the database needs for one metro, from the vendored files."""
    net = build_real_network(metro=metro, week=week)
    payload = load_vendor(metro)
    capability = (load_capability(metro) or {}).get("facilities", {})
    weeks: list[str] = payload["weeks"]
    idx = weeks.index(net.week)
    by_ccn = {f["ccn"]: f for f in payload["facilities"]}
    cfg_by_id = {c.hospital_id: c for c in net.configs}

    hospitals, capabilities, reports, resources = [], [], [], []
    for ccn, cfg in cfg_by_id.items():
        fac = by_ccn[ccn]
        hospitals.append({
            "hospital_id": ccn, "name": cfg.name, "address": fac.get("address"),
            "city": fac.get("city"), "zip": fac.get("zip"),
            "lat": fac["lat"], "lon": fac["lon"], "tier": cfg.tier,
            "source": f"{payload['source']['dataset']} ({payload['source']['publisher']})",
        })
        from_pos = ccn in capability
        for spec in sorted(s.value for s in cfg.specialties):
            capabilities.append({
                "hospital_id": ccn, "specialty": spec,
                "source": f"{POS} (service certification codes)" if from_pos
                          else "tier guess (no POS record)",
            })
        s = fac["series"]
        for i, wk in enumerate(weeks):
            reports.append({
                "hospital_id": ccn, "week_start": wk,
                "inpatient_beds": s["beds"][i], "inpatient_used": s["used"][i],
                "icu_beds": s["icu"][i], "icu_used": s["icu_used"][i],
                "picu_beds": s["picu"][i], "ed_visits": s["ed_visits"][i],
            })

        beds = _nearest_reported(fac, "beds", idx) or 0.0
        defaults = default_occupancy(
            cfg.capacities, _nearest_reported(fac, "used", idx),
            _nearest_reported(fac, "icu_used", idx), beds)
        for kind, cap in cfg.capacities.items():
            cprov, csrc = _capacity_provenance(kind, from_pos)
            occ, oprov, osrc = defaults.get(kind, (0, "assumed", "no data"))
            resources.append({
                "hospital_id": ccn, "kind": kind.value, "capacity": cap,
                "capacity_provenance": cprov, "capacity_source": csrc,
                "default_occupied": occ, "default_provenance": oprov,
                "default_source": f"{osrc} (week of {net.week})",
            })

    travel = []
    routes = load_routes(metro)
    if routes:
        ccns = routes["ccns"]
        for i, a in enumerate(ccns):
            for j, b in enumerate(ccns):
                if a in cfg_by_id and b in cfg_by_id and i != j:
                    travel.append({
                        "from_id": a, "to_id": b,
                        "road_minutes": round(routes["durations_seconds"][i][j] / 60.0, 2),
                        "source": "OSRM over OpenStreetMap (free-flow driving time)",
                    })

    return ReferenceData(
        metro=metro, week=net.week, source=payload["source"], hospitals=hospitals,
        capabilities=capabilities, weekly_reports=reports, travel_times=travel,
        resources=resources,
    )
