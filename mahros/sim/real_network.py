"""Turn vendored real facility records into a simulable hospital network.

This is the module that decides, for every number the simulator consumes,
whether it is **observed**, **derived** from something observed, or
**assumed** by the authors. That three-way split is the point of the module.
`provenance_table()` renders it, `docs/DATA_PROVENANCE.md` quotes it, and no
parameter is allowed to reach the simulation without a label.

The rule the split enforces:

    A number is only "real" if it can be traced to a specific field of a
    specific record in a public dataset. Everything else is an assumption
    and must be named as one.

What comes out observed
-----------------------
Facility identity, name, coordinates, inpatient beds, staffed adult ICU beds,
paediatric ICU capability, and the occupancy each hospital actually ran at.

What comes out assumed
----------------------
HDU/step-down beds, operating-room slots, ventilators and cath labs are simply
not in the HHS dataset, and neither is any specialty except paediatrics. They
are generated from declared ratios, every one of which is a named constant in
this file so it can be swept. Replacing these with a CMS join on the CCN is the
single highest-value next step.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

from ..calibration.capability import (
    cath_lab_rooms,
    load_capability,
    onsite,
)
from ..calibration.facilities import load_vendor
from ..core.types import ResourceType, Specialty
from ..hospital.hospital import HospitalConfig

Provenance = Literal["observed", "derived", "assumed"]


# --------------------------------------------------------------------------- #
# Declared assumptions -- every one of these is an author decision
# --------------------------------------------------------------------------- #

#: Step-down / high-dependency beds per staffed adult ICU bed. Not reported in
#: HHS data. Step-down units are typically sized at roughly half again the ICU
#: they decant into.
HDU_PER_ICU = 0.50

#: Ventilators per staffed adult ICU bed. Most ICU beds are ventilator-capable
#: but not every one is ventilator-equipped simultaneously.
VENTILATORS_PER_ICU = 0.80

#: Inpatient beds served by one operating-room slot. A 400-bed hospital getting
#: ~13 concurrent OR slots is the intended magnitude.
BEDS_PER_OR_SLOT = 30.0

#: Transfer coordinators (the scarce human resource that makes the phone-tree
#: baseline realistic) per inpatient bed, floored at 1.
COORDINATORS_PER_BED = 1 / 150.0

#: Tier boundaries in inpatient beds. These are the p55 / p88 percentiles of the
#: *national* bed distribution across 3,186 short-term acute hospitals, taken
#: from the existing vendored HHS summary. Using national percentiles rather
#: than percentiles of the study cluster means the tier of a hospital does not
#: depend on which metro it was drawn from.
TIER2_MIN_BEDS = 154.9
TIER3_MIN_BEDS = 426.0

#: Size alone does not make a referral centre, and treating it as if it did
#: produces false capability claims. The Woman's Hospital of Texas reports 513
#: inpatient beds but only 4 staffed adult ICU beds: a large specialty hospital,
#: not somewhere you send a crashing cardiac patient. A tier-3 facility must
#: therefore also show real critical-care depth. This threshold sits below the
#: national tier-2/3 median ICU ratios (0.118 / 0.127) and well above the
#: specialty-hospital cases it exists to exclude.
TIER3_MIN_ICU_RATIO = 0.05

#: Mean length of stay the simulator's own admission generator produces, in
#: hours, measured empirically from `PatientGenerator.initial_admission`
#: (200k draws). This is the *unscaled* figure.
MODEL_MEAN_LOS_HOURS = 50.67

#: Mean length of stay assumed for a US short-term acute-care admission, in
#: hours. ~4.6 days is the conventional figure for US community hospitals over
#: this period.
#:
#: This matters more than it looks. Occupancy is fitted by Little's Law
#: (L = lambda * W), so W sets the admission throughput needed to hold a given
#: bed state. At the simulator's native 50.7 h -- about 2.1 days, less than half
#: the real figure -- reproducing Memorial Hermann TMC's observed occupancy took
#: 421 admissions/day, roughly double what that hospital really sees. Scaling
#: LOS to the real value fixes both at once: occupancy *and* throughput land in
#: the right place.
#:
#: Declared an assumption, not an observation: it is not measured from the HHS
#: data, which reports no admission counts. It is exposed as a scenario
#: parameter so it can be swept, and calibrating it against HCUP or MIMIC-IV
#: remains follow-on work.
REAL_MEAN_LOS_HOURS = 110.4

#: Multiplier applied to the simulator's LOS distribution for real networks.
#: Synthetic scenarios keep 1.0, so every pre-existing synthetic result stays
#: bit-identical.
LOS_SCALE = REAL_MEAN_LOS_HOURS / MODEL_MEAN_LOS_HOURS

#: Plausibility bound on implied admissions per staffed bed per day. At a 4.6
#: day stay a bed turns over about 0.22 times a day; 0.35 leaves generous room
#: for short-stay-heavy facilities before flagging. Size-independent, so it does
#: not simply fire on every large hospital.
ADMISSIONS_PER_BED_PER_DAY_WARN = 0.35

#: Highest occupancy the arrival fit will target.
#:
#: Ten of the 27 Houston facilities report weekly-average inpatient occupancy at
#: or above 0.97, and four report exactly 1.000. A stationary loss system cannot
#: sit at 100% occupancy at any finite arrival rate -- driving the fit there
#: demands enormous offered load and implies the hospital turns away three
#: arrivals in four, which is not what a full hospital does. It holds patients
#: in the emergency department and diverts at the margin.
#:
#: Facilities reporting at or above this ceiling are therefore fitted to the
#: ceiling and treated as "effectively full". The residual under-shoot at those
#: hospitals is real, is visible in the occupancy validation, and is reported
#: rather than tuned away.
OCCUPANCY_FIT_CEILING = 0.95

#: Mean length of stay by bed pool, in minutes, before `LOS_SCALE`. These are
#: the simulator's own admission-mix figures, kept here so the per-pool arrival
#: fit and the generator agree on the same numbers.
POOL_MEAN_LOS_MINUTES = {
    ResourceType.WARD_BED: 2600.0,
    ResourceType.HDU_BED: 1800.0,
    ResourceType.ICU_BED: 2400.0,
}

#: Lognormal draws with sigma=0.6 have mean exp(sigma^2/2) times their median
#: parameter, so the realised mean stay is ~19.7% above the figure above. The
#: arrival fit has to use the realised mean, not the parameter.
_LOGNORMAL_MEAN_INFLATION = math.exp(0.6 ** 2 / 2)

def erlang_b(servers: int, offered_load: float) -> float:
    """Blocking probability for a finite-server loss system.

    Uses the numerically stable recursion
    ``B(k, a) = a B(k-1, a) / (k + a B(k-1, a))`` starting from ``B(0, a) = 1``,
    which avoids the overflow of the factorial form at realistic bed counts.
    """
    if servers <= 0:
        return 1.0
    b = 1.0
    for k in range(1, servers + 1):
        b = (offered_load * b) / (k + offered_load * b)
    return b


def offered_load_for_occupancy(servers: int, target_occupancy: float) -> float:
    """Offered load whose *carried* load fills `target_occupancy` of the pool.

    Solves ``a (1 - B(c, a)) = target_occupancy * c`` by bisection. Monotone in
    ``a``, so bisection is safe and needs no derivative. A pool cannot carry
    more than every server busy, so a target of 1.0 is unreachable at finite
    load; it is clamped just below, and the residual undershoot is reported by
    the validation rather than hidden.
    """
    if servers <= 0 or target_occupancy <= 0:
        return 0.0
    target_carried = min(target_occupancy, 0.995) * servers
    # Grow the bracket until it actually contains the root. A fixed multiple of
    # `servers` silently saturates for targets close to 1 on small pools, which
    # returns the bound itself rather than a solution.
    lo, hi = 0.0, max(2.0 * servers, 8.0)
    for _ in range(60):
        if hi * (1.0 - erlang_b(servers, hi)) >= target_carried:
            break
        hi *= 2.0
    for _ in range(80):
        mid = (lo + hi) / 2
        carried = mid * (1.0 - erlang_b(servers, mid))
        if carried < target_carried:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


#: POS service field -> the specialty it evidences. Every one of these is an
#: observed certification record, not an inference from hospital size.
#:
#: The tier-based guess this replaces was badly wrong, and in a way that
#: flattered large hospitals: it handed cardiac, neuro, trauma and obstetrics to
#: every tier-3 facility and none to any tier-1. In the real record trauma is
#: certified at only 8 of 27 Houston hospitals and does not track size at all --
#: Oakbend Medical Center has it at 95 beds while CHI St Luke's Baylor, at 474
#: beds, does not.
_POS_SPECIALTY_FIELDS: dict[str, Specialty] = {
    "OPEN_HRT_SRGRY_SRVC_CD": Specialty.CARDIAC,
    "CRDC_CTHRTZTN_LAB_SRVC_CD": Specialty.CARDIAC,
    "NRSRGCL_SRVC_CD": Specialty.NEURO,
    "SHCK_TRMA_SRVC_CD": Specialty.TRAUMA,
    "OB_SRVC_CD": Specialty.OBSTETRIC,
    "PED_SRVC_CD": Specialty.PAEDIATRIC,
    "PED_ICU_SRVC_CD": Specialty.PAEDIATRIC,
    "BURN_CARE_UNIT_SRVC_CD": Specialty.BURNS,
}


def specialties_from_pos(row: dict) -> set[Specialty]:
    """Observed capability for one facility. GENERAL is universal."""
    out = {Specialty.GENERAL}
    for field, specialty in _POS_SPECIALTY_FIELDS.items():
        if onsite(row, field):
            out.add(specialty)
    return out


#: Specialties by tier. Retained only as the fallback for a network with no
#: vendored capability records, and for the synthetic scenarios. Any run that
#: falls back to it says so in `RealNetwork.warnings`.
_TIER_SPECIALTIES: dict[int, set[Specialty]] = {
    1: {Specialty.GENERAL},
    2: {Specialty.GENERAL, Specialty.OBSTETRIC, Specialty.TRAUMA},
    3: {Specialty.GENERAL, Specialty.CARDIAC, Specialty.NEURO,
        Specialty.TRAUMA, Specialty.OBSTETRIC},
}

#: Cath labs, assumed only at tier 3 (cardiac-capable) facilities.
CATH_LABS_TIER3 = 2


# --------------------------------------------------------------------------- #
# Provenance bookkeeping
# --------------------------------------------------------------------------- #

@dataclass
class FieldProvenance:
    field: str
    provenance: Provenance
    source: str

    def as_dict(self) -> dict[str, str]:
        return {"field": self.field, "provenance": self.provenance,
                "source": self.source}


HHS = "HHS/CDC NHSN facility reporting"

PROVENANCE: list[FieldProvenance] = [
    FieldProvenance("hospital_id (CCN)", "observed", f"{HHS}: ccn"),
    FieldProvenance("name", "observed", f"{HHS}: hospital_name"),
    FieldProvenance("x, y (km)", "observed",
                    f"{HHS}: geocoded_hospital_address, projected about the "
                    f"cluster centroid"),
    FieldProvenance("ICU_BED", "observed",
                    f"{HHS}: total_staffed_adult_icu_beds_7_day_avg"),
    FieldProvenance("WARD_BED", "derived",
                    f"{HHS}: inpatient_beds minus ICU and HDU"),
    FieldProvenance("tier", "derived",
                    "observed inpatient beds against national p55/p88 bed "
                    "percentiles, gated on observed ICU depth"),
    FieldProvenance("PAEDIATRIC specialty", "observed",
                    f"{HHS}: total_staffed_pediatric_icu_beds_7_day_avg > 0"),
    FieldProvenance("base_arrival_rate", "derived",
                    f"per-pool Erlang-B inversion of {HHS} observed occupancy "
                    f"against the assumed mean length of stay"),
    FieldProvenance("admission_mix", "derived",
                    f"{HHS} observed bed composition, normalised by pool "
                    f"length of stay"),
    FieldProvenance("mean length of stay", "assumed",
                    f"{REAL_MEAN_LOS_HOURS:.0f} h (~4.6 d) typical US acute "
                    f"ALOS; not reported in HHS data; swept"),
    FieldProvenance("travel time", "observed",
                    "OSRM routed driving time over OpenStreetMap between the "
                    "real coordinates (free-flow; no live traffic)"),
    FieldProvenance("handover overhead", "assumed",
                    f"{12.0:.0f} min added per journey for patient packaging "
                    f"and crew handover; not reported anywhere; swept"),
    FieldProvenance("HDU_BED", "assumed",
                    f"{HDU_PER_ICU} x ICU beds -- not in any public dataset"),
    FieldProvenance("VENTILATOR", "assumed",
                    f"{VENTILATORS_PER_ICU} x ICU beds -- not reported"),
    FieldProvenance("OR_SLOT", "assumed",
                    f"1 per {BEDS_PER_OR_SLOT:.0f} inpatient beds -- not reported"),
    FieldProvenance("CATH_LAB", "observed",
                    "CMS Provider of Services Q1 2021: "
                    "CRDC_CTHRTZTN_PRCDR_ROOMS_CNT (actual room count)"),
    FieldProvenance("CARDIAC / NEURO / TRAUMA / OBSTETRIC / BURNS", "observed",
                    "CMS Provider of Services Q1 2021 service certification "
                    "codes, joined on CCN"),
    FieldProvenance("coordinators", "assumed",
                    f"1 per {1 / COORDINATORS_PER_BED:.0f} inpatient beds"),
]


def provenance_table() -> str:
    """Render the observed / derived / assumed split as a markdown table."""
    order = {"observed": 0, "derived": 1, "assumed": 2}
    rows = sorted(PROVENANCE, key=lambda p: (order[p.provenance], p.field))
    out = ["| Field | Provenance | Source |", "|---|---|---|"]
    for p in rows:
        tag = {"observed": "**observed**", "derived": "derived",
               "assumed": "_assumed_"}[p.provenance]
        out.append(f"| `{p.field}` | {tag} | {p.source} |")
    counts = {k: sum(1 for p in PROVENANCE if p.provenance == k)
              for k in ("observed", "derived", "assumed")}
    out.append("")
    out.append(f"{counts['observed']} observed, {counts['derived']} derived, "
               f"{counts['assumed']} assumed.")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Network construction
# --------------------------------------------------------------------------- #

@dataclass
class RealNetwork:
    """A network of real facilities, plus everything needed to audit it."""

    configs: list[HospitalConfig]
    metro: str
    label: str
    week: str
    weeks: list[str]
    source: dict[str, Any]
    selection: dict[str, Any]
    excluded: list[dict[str, Any]]
    #: ccn -> observed values at `week`, kept for validation.
    observed: dict[str, dict[str, float]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def n_hospitals(self) -> int:
        return len(self.configs)


def ccn_of(fac: dict) -> str:
    return fac["ccn"]


def _tier_for(beds: float, icu: float) -> int:
    """Tier from observed bed count *and* observed critical-care depth.

    Both inputs are observed; the thresholds are declared assumptions.
    """
    icu_ratio = (icu / beds) if beds > 0 else 0.0
    if beds >= TIER3_MIN_BEDS and icu_ratio >= TIER3_MIN_ICU_RATIO:
        return 3
    if beds >= TIER2_MIN_BEDS and icu > 0:
        return 2
    return 1


def _series_at(fac: dict, key: str, idx: int) -> float | None:
    vals = fac["series"].get(key) or []
    return vals[idx] if 0 <= idx < len(vals) else None


def _nearest_reported(fac: dict, key: str, idx: int) -> float | None:
    """Value at `idx`, else the closest reported week either side.

    A hospital that did not report in one week is not a hospital that vanished.
    Falling back to its nearest reported week is preferable to dropping the
    facility, and the distance fallen back is surfaced as a warning.
    """
    direct = _series_at(fac, key, idx)
    if direct is not None:
        return direct
    vals = fac["series"].get(key) or []
    for offset in range(1, len(vals)):
        for j in (idx - offset, idx + offset):
            if 0 <= j < len(vals) and vals[j] is not None:
                return vals[j]
    return None


def build_real_network(
    metro: str = "houston",
    week: str | None = None,
    capacity_scale: float = 1.0,
) -> RealNetwork:
    """Build a network of real hospitals as they stood in a given week.

    `capacity_scale` is retained so the ICU-scarcity counterfactual can be run
    on the real topology. At 1.0 -- the default for all headline results --
    every bed count is the one the facility reported.
    """
    payload = load_vendor(metro)
    capability = load_capability(metro)
    weeks: list[str] = payload["weeks"]
    sel = payload["selection"]
    target = week or sel["reference_week"]
    if target not in weeks:
        raise ValueError(
            f"week {target!r} not in vendored data for {metro} "
            f"({weeks[0]} .. {weeks[-1]})")
    idx = weeks.index(target)

    lat0 = sel["centroid_lat"]
    lon0 = sel["centroid_lon"]
    km_per_deg_lat = 110.574
    km_per_deg_lon = 111.320 * math.cos(math.radians(lat0))

    configs: list[HospitalConfig] = []
    observed: dict[str, dict[str, float]] = {}
    warnings: list[str] = []

    for fac in payload["facilities"]:
        beds = _nearest_reported(fac, "beds", idx)
        if beds is None or beds < 1:
            warnings.append(f"{fac['name']}: no bed count in any week; skipped")
            continue
        icu = _nearest_reported(fac, "icu", idx) or 0.0
        used = _nearest_reported(fac, "used", idx)
        picu = _nearest_reported(fac, "picu", idx) or 0.0

        if _series_at(fac, "beds", idx) is None:
            warnings.append(
                f"{fac['name']}: bed count missing in {target}, used nearest "
                f"reported week")

        tier = _tier_for(beds, icu)

        # -- capacities ---------------------------------------------------- #
        # ICU is observed. HDU / ventilators / OR / cath lab are assumed from
        # declared ratios because HHS reports none of them.
        icu_beds = max(0, int(round(icu * capacity_scale)))
        hdu_beds = max(0, int(round(icu * HDU_PER_ICU * capacity_scale)))
        # Inpatient beds in the source include ICU, so ward is the remainder.
        ward_beds = max(1, int(round((beds - icu) * capacity_scale)) - hdu_beds)
        caps: dict[ResourceType, int] = {
            ResourceType.WARD_BED: ward_beds,
            ResourceType.HDU_BED: hdu_beds,
            ResourceType.ICU_BED: icu_beds,
            ResourceType.OR_SLOT: max(1, int(round(beds / BEDS_PER_OR_SLOT))),
            ResourceType.VENTILATOR: max(
                0, int(round(icu * VENTILATORS_PER_ICU * capacity_scale))),
        }
        # -- capability: observed where possible ---------------------------- #
        pos_row = (capability or {}).get("facilities", {}).get(ccn_of(fac))
        if pos_row is not None:
            specialties = specialties_from_pos(pos_row)
            rooms = cath_lab_rooms(pos_row)
            if rooms > 0:
                caps[ResourceType.CATH_LAB] = max(
                    1, int(round(rooms * capacity_scale)))
        else:
            specialties = set(_TIER_SPECIALTIES[tier])
            if tier == 3:
                caps[ResourceType.CATH_LAB] = CATH_LABS_TIER3
            warnings.append(
                f"{fac['name']}: no POS capability record; specialties fell "
                f"back to the tier guess")
        # Staffed paediatric ICU beds in the capacity record are a second,
        # independent signal for the same capability.
        if picu > 0:
            specialties.add(Specialty.PAEDIATRIC)

        # -- arrival rate and admission mix, fitted per bed pool ---------- #
        # Little's Law applied to each pool separately: L_r = lambda_r * W_r.
        # Targeting the same observed occupancy in every pool gives
        #     lambda_r = occupancy * capacity_r / W_r
        # and the admission mix is just those rates normalised. Fitting per
        # pool rather than in aggregate is what stops an ICU-heavy hospital
        # from overflowing its ward while its ICU sits empty -- the failure the
        # occupancy validation exposed.
        if used is None:
            occ_ratio = 0.70
            warnings.append(
                f"{fac['name']}: occupancy not reported in {target}; arrival "
                f"rate fitted to the 0.70 network mean instead")
        else:
            occ_ratio = min(used / beds, 1.0)

        # The reported figure is never overwritten: the fit is capped, the
        # observation is not. Conflating the two would hide exactly the gap the
        # validation exists to measure.
        reported_occ = occ_ratio
        if occ_ratio > OCCUPANCY_FIT_CEILING:
            warnings.append(
                f"{fac['name']}: reported occupancy {occ_ratio:.3f} exceeds the "
                f"{OCCUPANCY_FIT_CEILING:.2f} fitting ceiling; fitted to the "
                f"ceiling and treated as effectively full")
            occ_ratio = OCCUPANCY_FIT_CEILING

        pool_rates: dict[ResourceType, float] = {}
        for pool, los_minutes in POOL_MEAN_LOS_MINUTES.items():
            cap = caps.get(pool, 0)
            if cap <= 0:
                continue
            w_hours = (los_minutes * _LOGNORMAL_MEAN_INFLATION * LOS_SCALE) / 60.0
            # Offered load, corrected for the arrivals this pool will turn away
            # when it is full. Without the correction the fit undershoots, worst
            # at the fullest hospitals.
            offered = offered_load_for_occupancy(cap, occ_ratio)
            pool_rates[pool] = offered / w_hours

        arrival_rate = sum(pool_rates.values())
        admission_mix = {
            pool: (rate / arrival_rate, POOL_MEAN_LOS_MINUTES[pool])
            for pool, rate in pool_rates.items()
        } if arrival_rate > 0 else None

        # Offered load legitimately exceeds admitted load at a full hospital --
        # that is what blocking means, and Erlang-B puts it there deliberately.
        # The quantity that has to stay physically sensible is the load actually
        # *carried*: at a ~4.6 day stay a bed turns over about 0.22 times a day.
        carried_per_hour = sum(
            offered * (1.0 - erlang_b(caps[pool], offered))
            / ((POOL_MEAN_LOS_MINUTES[pool] * _LOGNORMAL_MEAN_INFLATION
                * LOS_SCALE) / 60.0)
            for pool, offered in (
                (p, offered_load_for_occupancy(caps[p], occ_ratio))
                for p in pool_rates)
        )
        admissions_per_day = carried_per_hour * 24.0
        per_bed = admissions_per_day / max(beds * capacity_scale, 1.0)
        if per_bed > ADMISSIONS_PER_BED_PER_DAY_WARN:
            warnings.append(
                f"{fac['name']}: fitted arrival rate implies "
                f"{admissions_per_day:.0f} admitted patients/day "
                f"({per_bed:.2f} per bed per day), above the "
                f"{ADMISSIONS_PER_BED_PER_DAY_WARN:.2f} plausibility bound")

        ccn = fac["ccn"]
        configs.append(HospitalConfig(
            hospital_id=ccn,
            name=fac["name"].title(),
            tier=tier,
            x=(fac["lon"] - lon0) * km_per_deg_lon,
            y=(fac["lat"] - lat0) * km_per_deg_lat,
            capacities=caps,
            specialties=specialties,
            coordinators=max(1, int(round(beds * COORDINATORS_PER_BED))),
            base_arrival_rate=arrival_rate,
            admission_mix=admission_mix,
        ))
        observed[ccn] = {
            #: What the arrival rate was fitted to (capped at the ceiling).
            "fitted_occupancy": occ_ratio,
            "inpatient_beds": beds,
            "inpatient_beds_used": used if used is not None else float("nan"),
            #: What the facility actually reported. Never capped.
            "occupancy": reported_occ,
            "adult_icu_beds": icu,
            "paediatric_icu_beds": picu,
            "km_from_centroid": fac["km_from_centroid"],
        }

    if not configs:
        raise RuntimeError(f"no usable facilities for {metro} in week {target}")

    return RealNetwork(
        configs=configs, metro=metro, label=sel["label"], week=target,
        weeks=weeks, source=payload["source"], selection=sel,
        excluded=payload["excluded"], observed=observed, warnings=warnings,
    )
