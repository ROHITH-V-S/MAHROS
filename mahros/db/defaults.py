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
from ..sim.warm_start import default_occupancy

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
