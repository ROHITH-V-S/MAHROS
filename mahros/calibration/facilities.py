"""Build a *real* hospital network from federal facility-level reporting.

Why this module exists
----------------------
`mahros/sim/scenario.py` can generate a synthetic network from three tier
templates. That is useful for fast, offline, deterministic unit tests, and it is
honest about being synthetic. It is *not* an acceptable substrate for headline
results, because nothing in it is observed: the bed counts, the coordinates and
the names are all invented.

This module replaces the invented parts with observation. Every hospital in a
real network is a real, named, CMS-certified facility, at its real coordinates,
with the bed and ICU counts it actually reported to the federal government, and
the occupancy it actually ran at, week by week.

Source
------
*COVID-19 Reported Patient Impact and Hospital Capacity by Facility*
(HHS / CDC NHSN), ``https://healthdata.gov/resource/anag-cw7u.json``.
US federal public domain (``USGOV_WORKS``), dataset provenance flag ``official``.
Facility-level operational data; **contains no patient-level information**.

What this data does and does not support
----------------------------------------
Observed, and used directly:

* facility identity (CCN), name, address, geocoded coordinates
* inpatient beds, inpatient beds used
* staffed adult ICU beds, adult ICU occupancy
* staffed paediatric ICU beds -- the one *observed* capability signal
* emergency department visits

Not in this dataset at all, and therefore derived or assumed downstream:

* HDU / step-down beds, operating-room slots, ventilators, cath labs
* every specialty except paediatrics

Known limitations, stated here so they reach the paper rather than a reviewer:

1. Self-reported by hospitals via HHS TeleTracking or state health
   departments. Not independently audited.
2. Collection ended 2024-05-03 when the reporting mandate lapsed. There is no
   facility-level successor; CDC's current NHSN respiratory dataset is
   jurisdiction-level.
3. Excludes VA, Indian Health Service, DoD, psychiatric and rehabilitation
   facilities by design. A real Houston transfer network would include VA
   hospitals; this one cannot.
4. Some CCNs report on behalf of several physical sites. Those are excluded by
   an explicit, reasoned list -- see ``SUSPECTED_AGGREGATES`` -- never silently
   dropped.
5. Bed counts are 7-day averages of *staffed* beds, so they move week to week
   and may exceed licensed capacity during a surge.
"""

from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
ENDPOINT = "https://healthdata.gov/resource/anag-cw7u.json"

#: CDC suppresses small counts as -999999 to prevent facility re-identification.
#: These are *missing*, not zero. Treating them as zero would manufacture ICU
#: scarcity the data does not show.
SUPPRESSED = -999999

#: Fields pulled per facility-week.
FIELDS = [
    "hospital_pk", "ccn", "hospital_name", "address", "city", "state", "zip",
    "geocoded_hospital_address", "hospital_subtype", "is_metro_micro",
    "collection_week", "is_corrected",
    "inpatient_beds_7_day_avg", "inpatient_beds_7_day_coverage",
    "inpatient_beds_used_7_day_avg", "inpatient_beds_used_7_day_coverage",
    "total_staffed_adult_icu_beds_7_day_avg",
    "total_staffed_adult_icu_beds_7_day_coverage",
    "staffed_adult_icu_bed_occupancy_7_day_avg",
    "total_staffed_pediatric_icu_beds_7_day_avg",
    "previous_day_total_ed_visits_7_day_sum",
]


# --------------------------------------------------------------------------- #
# Facilities excluded by judgement, with the reason recorded
# --------------------------------------------------------------------------- #

#: A CCN identifies a certified *provider*, which is not always one building.
#: These rows carry valid CCNs and real addresses, but the bed counts they
#: report are implausible for the single facility at that address and are
#: consistent with system-wide reporting. Excluding them is a judgement call, so
#: it is declared here rather than buried in a filter expression, and the
#: experiments can re-run with them included to show it does not drive any
#: result.
SUSPECTED_AGGREGATES: dict[str, str] = {
    "450184": (
        "MEMORIAL HERMANN HOSPITAL SYSTEM reports 1,526 inpatient beds at "
        "1635 North Loop West, the address of Memorial Hermann Greater Heights "
        "(~250 beds). Consistent with system-wide reporting under one CCN, not "
        "a single facility."
    ),
    "450289": (
        "HARRIS HEALTH SYSTEM reports 546 inpatient beds at 2525 Holly Hall, an "
        "administrative address. Harris Health operates Ben Taub and LBJ as "
        "separate hospitals; this row aggregates them."
    ),
}

#: Name fragments that *flag* a row for aggregate review. Flagging is not
#: exclusion: a flagged facility not listed in SUSPECTED_AGGREGATES is kept, and
#: the flag is reported so a human can adjudicate it.
AGGREGATE_HINTS = ("HOSPITAL SYSTEM", "HEALTH SYSTEM", "HEALTHCARE SYSTEM")


# --------------------------------------------------------------------------- #
# Metro selection
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class MetroSpec:
    """A declared, sweepable choice of study region.

    Every field here is an author decision, not something the data dictates.
    They are named and defaulted in one place so a reviewer can see exactly what
    was chosen, and so sensitivity analysis can vary them.
    """

    key: str
    label: str
    state: str
    centroid_lat: float
    centroid_lon: float
    radius_km: float = 40.0
    min_inpatient_beds: float = 25.0
    #: Only short-term acute care. Long-term and psychiatric facilities do not
    #: participate in the escalation-transfer problem MAHROS models.
    subtypes: tuple[str, ...] = ("Short Term",)
    rationale: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "label": self.label, "state": self.state,
            "centroid_lat": self.centroid_lat, "centroid_lon": self.centroid_lon,
            "radius_km": self.radius_km,
            "min_inpatient_beds": self.min_inpatient_beds,
            "subtypes": list(self.subtypes),
            "rationale": self.rationale,
            "chosen_by": "authors -- not derived from the data; swept in "
                         "sensitivity analysis",
        }


METROS: dict[str, MetroSpec] = {
    "houston": MetroSpec(
        key="houston", label="Houston metro, TX", state="TX",
        # Texas Medical Center: the region's tertiary hub.
        centroid_lat=29.7080, centroid_lon=-95.4002,
        radius_km=40.0, min_inpatient_beds=25.0,
        rationale=(
            "A dense tertiary hub (Texas Medical Center) surrounded by "
            "community hospitals out to ~38 km -- the hub-and-spoke topology "
            "that makes escalation transfer a real problem. Chosen by the "
            "authors; radius and bed floor are swept in sensitivity analysis."
        ),
    ),
    # Further metros exist to show a result is not a Houston artefact.
    "chicago": MetroSpec(
        key="chicago", label="Chicago metro, IL", state="IL",
        centroid_lat=41.8789, centroid_lon=-87.6359,
        rationale="Independent replication region with a different payer and "
                  "ownership mix.",
    ),
    "phoenix": MetroSpec(
        key="phoenix", label="Phoenix metro, AZ", state="AZ",
        centroid_lat=33.4806, centroid_lon=-112.0742,
        rationale="Independent replication region, lower facility density.",
    ),
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def num(raw: Any) -> float | None:
    """Parse a numeric field, returning None for missing or suppressed values."""
    if raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    return None if val <= SUPPRESSED / 2 else val


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _get(url: str, timeout: float = 180.0) -> list[dict]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _query(where: str, select: list[str], limit: int = 50000,
           order: str = "") -> list[dict]:
    parts = [f"$limit={limit}",
             "$select=" + urllib.parse.quote(",".join(select)),
             "$where=" + urllib.parse.quote(where)]
    if order:
        parts.append("$order=" + urllib.parse.quote(order))
    return _get(f"{ENDPOINT}?" + "&".join(parts))


def _coords(row: dict) -> tuple[float, float] | None:
    geo = row.get("geocoded_hospital_address") or {}
    coords = geo.get("coordinates") if isinstance(geo, dict) else None
    if not coords or len(coords) != 2:
        return None
    lon, lat = coords
    return float(lat), float(lon)


# --------------------------------------------------------------------------- #
# Selection and fetch
# --------------------------------------------------------------------------- #

def select_facilities(spec: MetroSpec, reference_week: str) -> tuple[list[dict], list[dict]]:
    """Pick the facilities in `spec`'s catchment as of `reference_week`.

    Returns ``(kept, excluded)``. Exclusions carry a reason string; nothing is
    dropped silently, because a filter a reviewer cannot see is exactly the
    problem this module exists to fix.
    """
    where = (f"collection_week='{reference_week}T00:00:00.000' "
             f"AND state='{spec.state}'")
    rows = _query(where, FIELDS)

    kept: list[dict] = []
    excluded: list[dict] = []
    for row in rows:
        name = (row.get("hospital_name") or "").strip()
        ccn = row.get("ccn") or row.get("hospital_pk") or ""

        def drop(reason: str) -> None:
            excluded.append({"ccn": ccn, "name": name, "reason": reason})

        if row.get("hospital_subtype") not in spec.subtypes:
            continue  # out of scope by design, not a judgement call: not logged
        latlon = _coords(row)
        if latlon is None:
            drop("no geocoded address in the source record")
            continue
        dist = haversine_km(spec.centroid_lat, spec.centroid_lon, *latlon)
        if dist > spec.radius_km:
            continue  # outside the declared catchment
        beds = num(row.get("inpatient_beds_7_day_avg"))
        if beds is None:
            drop("inpatient bed count missing or CDC-suppressed in the "
                 "reference week")
            continue
        if beds < spec.min_inpatient_beds:
            drop(f"{beds:.0f} inpatient beds is below the declared "
                 f"{spec.min_inpatient_beds:.0f}-bed floor")
            continue
        if ccn in SUSPECTED_AGGREGATES:
            drop(SUSPECTED_AGGREGATES[ccn])
            continue

        row["_lat"], row["_lon"] = latlon
        row["_km_from_centroid"] = round(dist, 2)
        row["_aggregate_flag"] = any(h in name.upper() for h in AGGREGATE_HINTS)
        kept.append(row)

    kept.sort(key=lambda r: -(num(r.get("inpatient_beds_7_day_avg")) or 0.0))
    return kept, excluded


def fetch_series(ccns: list[str]) -> dict[str, dict[str, dict]]:
    """Pull every reported week for the given facilities.

    Returns ``{ccn: {week: row}}``. This is the real demand trace: 213 weekly
    observations per facility spanning 2020-03-29 to 2024-04-21, including the
    COVID waves as they actually happened, which is what replaces the
    hand-tuned ``surge_multiplier`` / ``surge_windows`` of the synthetic
    scenarios.
    """
    quoted = ",".join(f"'{c}'" for c in ccns)
    rows = _query(f"hospital_pk in ({quoted})", FIELDS,
                  order="hospital_pk,collection_week")
    out: dict[str, dict[str, dict]] = {}
    for row in rows:
        pk = row.get("hospital_pk")
        week = (row.get("collection_week") or "")[:10]
        if pk and week:
            out.setdefault(pk, {})[week] = row
    return out


# --------------------------------------------------------------------------- #
# Vendoring
# --------------------------------------------------------------------------- #

#: Series fields carried into the vendored file, as (output key, source field).
#: Kept deliberately narrow: everything here is either used to build a hospital
#: or to validate the simulation against observation.
SERIES_FIELDS = [
    ("beds", "inpatient_beds_7_day_avg"),
    ("used", "inpatient_beds_used_7_day_avg"),
    ("icu", "total_staffed_adult_icu_beds_7_day_avg"),
    ("icu_used", "staffed_adult_icu_bed_occupancy_7_day_avg"),
    ("picu", "total_staffed_pediatric_icu_beds_7_day_avg"),
    ("ed_visits", "previous_day_total_ed_visits_7_day_sum"),
    ("beds_coverage", "inpatient_beds_7_day_coverage"),
    ("used_coverage", "inpatient_beds_used_7_day_coverage"),
]


def build_vendor_payload(spec: MetroSpec, reference_week: str) -> dict[str, Any]:
    """Fetch, filter and package a real metro network for offline use.

    The payload is self-describing on purpose. It carries its own provenance,
    the author decisions that produced it, and the exclusion list with reasons,
    so that the data file alone answers "where did these hospitals come from?"
    without anyone having to read the fetching code.
    """
    kept, excluded = select_facilities(spec, reference_week)
    if not kept:
        raise RuntimeError(
            f"no facilities selected for {spec.key} in week {reference_week}")

    series = fetch_series([r["hospital_pk"] for r in kept])
    weeks = sorted({w for per in series.values() for w in per})

    facilities: list[dict[str, Any]] = []
    for row in kept:
        pk = row["hospital_pk"]
        per_week = series.get(pk, {})
        packed: dict[str, list] = {key: [] for key, _ in SERIES_FIELDS}
        for week in weeks:
            wrow = per_week.get(week)
            for key, field_name in SERIES_FIELDS:
                packed[key].append(None if wrow is None
                                   else num(wrow.get(field_name)))
        facilities.append({
            "ccn": row.get("ccn") or pk,
            "hospital_pk": pk,
            "name": (row.get("hospital_name") or "").strip(),
            "address": (row.get("address") or "").strip(),
            "city": (row.get("city") or "").strip(),
            "state": (row.get("state") or "").strip(),
            "zip": (row.get("zip") or "").strip(),
            "lat": row["_lat"],
            "lon": row["_lon"],
            "km_from_centroid": row["_km_from_centroid"],
            "is_metro_micro": row.get("is_metro_micro"),
            "aggregate_name_flag": row["_aggregate_flag"],
            "series": packed,
        })

    return {
        "source": {
            "dataset": "COVID-19 Reported Patient Impact and Hospital Capacity "
                       "by Facility",
            "publisher": "U.S. Department of Health & Human Services / CDC NHSN",
            "endpoint": ENDPOINT,
            "license": "U.S. Public Domain (USGOV_WORKS)",
            "dataset_provenance_flag": "official",
            "collection_ended": "2024-05-03",
            "contains_patient_data": False,
            "note": "Facility-level operational reporting. Self-reported by "
                    "hospitals via HHS TeleTracking or state health "
                    "departments; not independently audited. Excludes VA, IHS, "
                    "DoD, psychiatric and rehabilitation facilities by design.",
            "suppression": "Counts of -999999 are CDC privacy suppression of "
                           "small numbers. Stored as null: missing, not zero.",
        },
        "selection": {
            **spec.as_dict(),
            "reference_week": reference_week,
            "reference_week_note": "The week used to decide which facilities "
                                   "are in the network and how big they are. "
                                   "Simulation weeks are chosen separately.",
        },
        "n_facilities": len(facilities),
        "excluded": excluded,
        "excluded_note": "Every facility removed by a judgement call is listed "
                         "here with its reason. Facilities outside the "
                         "catchment radius or of a different hospital_subtype "
                         "are out of scope by declared design and are not "
                         "listed individually.",
        "weeks": weeks,
        "facilities": facilities,
    }


def vendor_path(metro: str) -> Path:
    return DATA_DIR / f"us_{metro}_metro.json"


def write_vendor(spec: MetroSpec, reference_week: str,
                 path: Path | None = None) -> Path:
    payload = build_vendor_payload(spec, reference_week)
    out = path or vendor_path(spec.key)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return out


def load_vendor(metro: str = "houston", path: Path | None = None) -> dict[str, Any]:
    """Load a vendored real network. Offline; no network access required."""
    target = path or vendor_path(metro)
    if not target.exists():
        raise FileNotFoundError(
            f"no vendored network at {target}. Build it with:\n"
            f"    python experiments/fetch_network.py --metro {metro}")
    return json.loads(target.read_text(encoding="utf-8"))
