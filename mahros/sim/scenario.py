"""Hospital-network generation: synthetic, and real.

Two substrates live here.

**Real networks** (`real_metro` set on a scenario) are built from
`mahros.sim.real_network`: every hospital is a real, named, CMS-certified
facility at its real coordinates, with the bed and ICU counts it reported to
HHS and the occupancy it actually ran at in a given week. All headline results
use these.

**Synthetic networks** (the default) are generated from three tier templates.
They remain because they are fast, offline and deterministic, which is what unit
tests need, and because the ICU-scarcity counterfactual is a deliberate
departure from any observed network.

Everything in the synthetic path is a documented modelling assumption, not a
claim about a real health system. Parameters are collected in one place so `docs/MODEL_ASSUMPTIONS.md`
can cite them and a reviewer can check them. Distributions are seeded, so every
run is reproducible from `(scenario_name, seed)`.

The network shape is a realistic Indian-style district topology: many small
primary/district hospitals with no ICU depth, a few tertiary centres holding the
specialty capability. This is what makes escalation transfers necessary at all.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from ..core.types import (
    Acuity,
    DemographicGroup,
    Patient,
    ResourceType,
    Specialty,
)
from ..hospital.hospital import HospitalConfig  # noqa: F401  (also used in annotations)
from ..calibration.routing import HANDOVER_MINUTES, road_travel_minutes


@dataclass
class ScenarioConfig:
    name: str = "baseline"
    seed: int = 42
    n_hospitals: int = 12
    horizon_hours: float = 24 * 14        # two weeks
    # A district/metropolitan referral cluster, not a whole state. At 35 km
    # radius the furthest pair is ~90 min apart and the median ~40 min, which
    # is the regime where escalation transfers are actually clinically viable.
    region_km: float = 35.0
    ambulance_speed_kmh: float = 50.0     # blended road speed incl. traffic
    surge_multiplier: float = 1.0         # applied during surge windows
    surge_windows: list[tuple[float, float]] = field(default_factory=list)  # (start_h, end_h)
    # Fraction of admissions that deteriorate and need a higher level of care.
    # Most are absorbed in-house; only those the hospital cannot serve become
    # transfer requests. Tuned so the resulting *transfer* rate lands near the
    # ~3.5% of admissions reported for real interhospital transfers -- that
    # ratio is the model's main external calibration check.
    escalation_prob: float = 0.18
    rural_fraction: float = 0.45
    uninsured_fraction: float = 0.40
    # capacity scaling: <1 tightens the whole network (India ICU scarcity study)
    capacity_scale: float = 1.0

    # -- real-network substrate -------------------------------------------- #
    #: When set, the network is built from vendored real facility records
    #: instead of the tier templates below, and `n_hospitals`, `region_km` and
    #: the tier mix are ignored -- the real network decides them.
    real_metro: str | None = None
    #: Which reported week to instantiate the real network from. Bed counts,
    #: ICU counts and occupancy are all taken from this week, so the choice of
    #: week *is* the choice of scenario: a calm week and a surge week are the
    #: same hospitals under the load they really carried.
    real_week: str | None = None
    #: Multiplier on the length-of-stay distribution. Real networks scale this
    #: so the mean stay matches US acute ALOS (~4.6 days) rather than the
    #: simulator's native ~2.1 days; see real_network.LOS_SCALE for why that
    #: matters. Synthetic scenarios keep 1.0 and are bit-identical to before.
    los_scale: float = 1.0

    @property
    def is_real(self) -> bool:
        return self.real_metro is not None


# --------------------------------------------------------------------------- #
# Network construction
# --------------------------------------------------------------------------- #

_TIER_TEMPLATES = {
    1: {  # primary / small private -- can admit, cannot escalate
        "capacities": {ResourceType.WARD_BED: 30, ResourceType.HDU_BED: 4,
                       ResourceType.ICU_BED: 2, ResourceType.OR_SLOT: 1,
                       ResourceType.VENTILATOR: 2},
        "specialties": {Specialty.GENERAL},
        "coordinators": 1,
        # Arrival rates are set so the network sits near 70% baseline occupancy:
        # busy enough that escalations contend for capacity, slack enough that
        # the baseline is not already in permanent crisis. See docs/MODEL_ASSUMPTIONS.md.
        "arrival_rate": 0.54,
    },
    2: {  # district hospital
        "capacities": {ResourceType.WARD_BED: 80, ResourceType.HDU_BED: 12,
                       ResourceType.ICU_BED: 8, ResourceType.OR_SLOT: 3,
                       ResourceType.VENTILATOR: 6},
        "specialties": {Specialty.GENERAL, Specialty.OBSTETRIC, Specialty.TRAUMA},
        "coordinators": 2,
        "arrival_rate": 1.44,
    },
    3: {  # tertiary referral centre -- holds the scarce capability
        "capacities": {ResourceType.WARD_BED: 200, ResourceType.HDU_BED: 30,
                       ResourceType.ICU_BED: 26, ResourceType.OR_SLOT: 8,
                       ResourceType.VENTILATOR: 22, ResourceType.CATH_LAB: 2},
        "specialties": {Specialty.GENERAL, Specialty.CARDIAC, Specialty.NEURO,
                        Specialty.TRAUMA, Specialty.PAEDIATRIC, Specialty.OBSTETRIC},
        "coordinators": 3,
        "arrival_rate": 3.60,
    },
}

# Tier mix: mostly small hospitals, few tertiary centres.
_TIER_MIX = [1, 1, 1, 1, 2, 1, 2, 3, 1, 2, 1, 3, 1, 2, 1, 3, 1, 1, 2, 3]


def build_network(cfg: ScenarioConfig) -> list[HospitalConfig]:
    """Build the hospital network for a scenario.

    Dispatches to the real-facility builder when the scenario names a metro, so
    that every caller -- the runner, the live console, the experiments -- gets
    real hospitals without knowing anything about how they are loaded.
    """
    if cfg.is_real:
        from .real_network import build_real_network
        return build_real_network(
            metro=cfg.real_metro,
            week=cfg.real_week,
            capacity_scale=cfg.capacity_scale,
        ).configs
    return _build_synthetic_network(cfg)


def _build_synthetic_network(cfg: ScenarioConfig) -> list[HospitalConfig]:
    rng = random.Random(cfg.seed)
    configs: list[HospitalConfig] = []

    for i in range(cfg.n_hospitals):
        tier = _TIER_MIX[i % len(_TIER_MIX)]
        tpl = _TIER_TEMPLATES[tier]

        # Tertiary centres cluster centrally; smaller hospitals spread outward.
        if tier == 3:
            r = rng.uniform(0, cfg.region_km * 0.30)
        elif tier == 2:
            r = rng.uniform(cfg.region_km * 0.15, cfg.region_km * 0.60)
        else:
            r = rng.uniform(cfg.region_km * 0.25, cfg.region_km)
        theta = rng.uniform(0, 2 * math.pi)

        caps = {
            res: max(1, int(round(n * cfg.capacity_scale * rng.uniform(0.85, 1.15))))
            for res, n in tpl["capacities"].items()
        }

        configs.append(
            HospitalConfig(
                hospital_id=f"H{i:02d}",
                name=f"{'Primary' if tier == 1 else 'District' if tier == 2 else 'Tertiary'} Hospital {i:02d}",
                tier=tier,
                x=r * math.cos(theta),
                y=r * math.sin(theta),
                capacities=caps,
                specialties=set(tpl["specialties"]),
                coordinators=tpl["coordinators"],
                base_arrival_rate=tpl["arrival_rate"] * rng.uniform(0.85, 1.15),
                escalation_prob=cfg.escalation_prob,
            )
        )
    return configs


def travel_time_matrix(
    configs: list[HospitalConfig],
    speed_kmh: float,
    metro: str | None = None,
) -> dict[tuple[str, str], float]:
    """Inter-hospital travel time in minutes.

    Travel time is the most decision-relevant number in the model: a bid that
    cannot deliver the patient inside the clinical safe window is discarded
    before scoring rather than traded off, so this decides which hospitals are
    even eligible.

    When `metro` names a real network with a vendored OSRM matrix, real routed
    road times are used. Otherwise -- synthetic networks, or a real network
    whose routes have not been fetched -- it falls back to straight-line
    distance at `speed_kmh` plus a fixed handover overhead. Any pair the router
    could not reach falls back individually.
    """
    road = road_travel_minutes(metro) if metro else None

    out: dict[tuple[str, str], float] = {}
    for a in configs:
        for b in configs:
            key = (a.hospital_id, b.hospital_id)
            if a.hospital_id == b.hospital_id:
                out[key] = 0.0
                continue
            if road is not None and key in road:
                out[key] = road[key]
                continue
            d = math.hypot(a.x - b.x, a.y - b.y)
            out[key] = HANDOVER_MINUTES + (d / speed_kmh) * 60.0
    return out


# --------------------------------------------------------------------------- #
# Patient / escalation generation
# --------------------------------------------------------------------------- #

_FIRST = ["Arjun", "Priya", "Rahul", "Sneha", "Vikram", "Ananya", "Karthik",
          "Meera", "Rohan", "Divya", "Sanjay", "Kavya"]
_LAST = ["Sharma", "Reddy", "Nair", "Iyer", "Patel", "Khan", "Das", "Menon"]

# What escalation looks like, and how long it ties up the receiving resource.
_ESCALATION_PROFILES: list[tuple[ResourceType, Specialty, tuple[int, int], float]] = [
    # (resource, specialty, (acuity_low, acuity_high), mean LOS minutes)
    (ResourceType.ICU_BED,    Specialty.GENERAL,   (3, 5), 3600),
    (ResourceType.ICU_BED,    Specialty.NEURO,     (4, 5), 4320),
    (ResourceType.ICU_BED,    Specialty.PAEDIATRIC,(3, 5), 3000),
    (ResourceType.OR_SLOT,    Specialty.TRAUMA,    (4, 5), 300),
    (ResourceType.OR_SLOT,    Specialty.GENERAL,   (2, 4), 240),
    (ResourceType.CATH_LAB,   Specialty.CARDIAC,   (4, 5), 180),
    (ResourceType.VENTILATOR, Specialty.GENERAL,   (4, 5), 2880),
    (ResourceType.HDU_BED,    Specialty.GENERAL,   (2, 3), 2160),
    (ResourceType.HDU_BED,    Specialty.OBSTETRIC, (3, 4), 1440),
]

# Relative frequency of each profile.
_PROFILE_WEIGHTS = [0.20, 0.08, 0.07, 0.12, 0.10, 0.09, 0.11, 0.15, 0.08]


class PatientGenerator:
    def __init__(self, cfg: ScenarioConfig) -> None:
        self.cfg = cfg
        self.rng = random.Random(cfg.seed + 9973)
        self._n = 0

    def demographic(self) -> DemographicGroup:
        rural = self.rng.random() < self.cfg.rural_fraction
        uninsured = self.rng.random() < self.cfg.uninsured_fraction
        if rural:
            return DemographicGroup.RURAL_UNINSURED if uninsured else DemographicGroup.RURAL_INSURED
        return DemographicGroup.URBAN_UNINSURED if uninsured else DemographicGroup.URBAN_INSURED

    def make_patient(self, home_hospital: str) -> Patient:
        self._n += 1
        first = self.rng.choice(_FIRST)
        last = self.rng.choice(_LAST)
        # Free text deliberately seeded with identifiers so the privacy layer
        # has something real to strip. This is synthetic data only.
        notes = (
            f"Patient {first} {last}, MRN {home_hospital}-{self._n:06d}, "
            f"contact +91 98{self.rng.randint(10**7, 10**8 - 1)}. "
            f"Admitted {self.rng.randint(1, 28)}/0{self.rng.randint(1, 9)}/2026 "
            f"under Dr. {self.rng.choice(_LAST)}. Condition deteriorating."
        )
        return Patient(
            name=f"{first} {last}",
            mrn=f"{home_hospital}-{self._n:06d}",
            phone=f"+91 98{self.rng.randint(10**7, 10**8 - 1)}",
            age=max(1, int(self.rng.gauss(48, 22))),
            group=self.demographic(),
            home_hospital=home_hospital,
            notes=notes,
        )

    def escalation_profile(self) -> tuple[ResourceType, Specialty, Acuity, float]:
        resource, specialty, (lo, hi), mean_los = self.rng.choices(
            _ESCALATION_PROFILES, weights=_PROFILE_WEIGHTS, k=1
        )[0]
        acuity = Acuity(self.rng.randint(lo, hi))
        los = max(60.0, self.rng.lognormvariate(math.log(mean_los), 0.45))
        return resource, specialty, acuity, los * self.cfg.los_scale

    #: initial admission mix -> (resource, weight, mean LOS minutes)
    _ADMISSION_MIX = [
        (ResourceType.WARD_BED, 0.900, 2600.0),   # ~43 h
        (ResourceType.HDU_BED,  0.075, 1800.0),   # ~30 h
        (ResourceType.ICU_BED,  0.025, 2400.0),   # ~40 h
    ]

    def initial_admission(
        self, hospital: "HospitalConfig | None" = None
    ) -> tuple[ResourceType, float]:
        """Draw where a new admission lands and how long it stays.

        With no hospital given, uses the scenario-wide mix: most admissions
        start on a ward bed and stay a couple of days, and critical-care beds
        are left lightly loaded at admission on purpose, so that the escalation
        stream is what fills them.

        Real networks pass the hospital, which carries a mix fitted to its own
        observed bed composition. That matters: validating against reported
        occupancy showed the global mix systematically starving the ICU and
        overflowing the ward at ICU-heavy facilities.
        """
        mix = (hospital.admission_mix if hospital is not None
               and hospital.admission_mix else None)
        if mix:
            resources = list(mix)
            weights = [mix[r][0] for r in resources]
            resource = self.rng.choices(resources, weights=weights, k=1)[0]
            mean_los = mix[resource][1]
        else:
            resource, _, mean_los = self.rng.choices(
                self._ADMISSION_MIX,
                weights=[w for _, w, _ in self._ADMISSION_MIX], k=1)[0]
        los = max(120.0, self.rng.lognormvariate(math.log(mean_los), 0.6))
        return resource, los * self.cfg.los_scale

    def arrival_gap(self, rate_per_hour: float, t_minutes: float) -> float:
        """Exponential inter-arrival with a diurnal curve and optional surge."""
        hour = (t_minutes / 60.0) % 24
        # Admissions peak late morning and early evening; trough at 04:00.
        diurnal = 1.0 + 0.45 * math.sin((hour - 8.0) * math.pi / 12.0)
        surge = 1.0
        for start_h, end_h in self.cfg.surge_windows:
            if start_h * 60 <= t_minutes <= end_h * 60:
                surge = self.cfg.surge_multiplier
                break
        effective = max(0.05, rate_per_hour * diurnal * surge)
        return self.rng.expovariate(effective / 60.0)

    def escalation_delay(self) -> float:
        """Time from admission to deterioration. Most within the first 24h."""
        return max(15.0, self.rng.lognormvariate(math.log(420), 0.9))


# --------------------------------------------------------------------------- #
# Named scenarios used by the experiments
# --------------------------------------------------------------------------- #

# Scenarios are tuned so that the *escalation transfer* problem stays the thing
# under study. Pushing surge/scarcity harder tips the network into a different
# regime -- patients turned away at the door -- which is an ambulance-diversion
# problem, not one MAHROS claims to solve. Rejected-admission rate is the guard:
# keep it under ~25% or the transfer signal is drowned out.
SCENARIOS: dict[str, ScenarioConfig] = {
    "baseline": ScenarioConfig(
        name="baseline", n_hospitals=12, horizon_hours=24 * 14,
    ),
    "surge": ScenarioConfig(
        # Epidemic / festival / monsoon demand spike on an intact network.
        name="surge", n_hospitals=12, horizon_hours=24 * 14,
        surge_multiplier=1.9,
        surge_windows=[(24 * 3, 24 * 6), (24 * 9, 24 * 11)],
    ),
    "scarcity": ScenarioConfig(
        # India-style critical-care scarcity: same demand, materially less capacity.
        name="scarcity", n_hospitals=12, horizon_hours=24 * 14,
        capacity_scale=0.72,
    ),
    "surge_scarcity": ScenarioConfig(
        # The stress case the paper leads with: scarce network, demand spike.
        name="surge_scarcity", n_hospitals=12, horizon_hours=24 * 14,
        capacity_scale=0.78, surge_multiplier=1.7,
        surge_windows=[(24 * 3, 24 * 6), (24 * 9, 24 * 11)],
    ),
    "large_network": ScenarioConfig(
        # Scalability: does the protocol's message and staff cost stay sane?
        name="large_network", n_hospitals=30, horizon_hours=24 * 7,
        region_km=55.0, surge_multiplier=1.6,
        surge_windows=[(24 * 2, 24 * 4)],
    ),
    "smoke": ScenarioConfig(
        name="smoke", n_hospitals=6, horizon_hours=24 * 2, region_km=25.0,
    ),
}


# --------------------------------------------------------------------------- #
# Real networks -- the substrate for every headline result
# --------------------------------------------------------------------------- #

# Weeks are not chosen for narrative convenience. Every one below was picked
# from the 216 reported weeks by network-wide observed occupancy, restricted to
# weeks where at least 26 of the 27 facilities reported bed counts and at least
# 22 reported ICU, so that the load is measured rather than inferred. The
# spread they cover -- 0.75 to 0.96 inpatient occupancy -- is what this network
# really did, and it is materially tighter than the 0.70 the synthetic
# scenarios were tuned to.
#
# Note in particular that the busiest weeks are *not* the COVID waves. Houston
# ran fuller in late 2023 than it did in January 2021.

def _real(name: str, week: str, horizon_days: float = 14,
          capacity_scale: float = 1.0, metro: str = "houston",
          escalation_prob: float = 0.18) -> ScenarioConfig:
    from .real_network import LOS_SCALE
    return ScenarioConfig(
        name=name, real_metro=metro, real_week=week,
        horizon_hours=24 * horizon_days, los_scale=LOS_SCALE,
        capacity_scale=capacity_scale, escalation_prob=escalation_prob,
        # Real coordinates set the geography; region_km is unused but kept
        # truthful for anything that reports it.
        region_km=40.0,
    )


REAL_SCENARIOS: dict[str, ScenarioConfig] = {
    # Calmest fully-reported week in the series. 0.749 inpatient / 0.818 ICU.
    "houston_calm": _real("houston_calm", "2020-08-23"),
    # The reference week the facility selection and the existing HHS summary
    # calibration both use. 0.823 inpatient / 0.886 ICU.
    "houston_baseline": _real("houston_baseline", "2021-01-10"),
    # Median week across the whole series. 0.907 inpatient / 0.883 ICU.
    "houston_typical": _real("houston_typical", "2022-05-08"),
    # Delta wave peak, full reporting from all 27 facilities.
    # 0.951 inpatient / 0.951 ICU. This is the stress case.
    "houston_surge": _real("houston_surge", "2021-07-25"),
    # Post-pandemic capacity crisis: busier than any COVID week.
    # 0.955 inpatient / 0.921 ICU.
    "houston_post_pandemic": _real("houston_post_pandemic", "2023-12-10"),
    # The ICU-scarcity counterfactual, now run on real topology rather than an
    # invented one: real hospitals, real geography, capacity tightened to the
    # critical-care-scarce regime the India scenarios target. Declared as a
    # counterfactual, not as an observation of anywhere.
    "houston_icu_scarce": _real("houston_icu_scarce", "2021-07-25",
                                capacity_scale=0.72),
    # Fast real-network smoke test.
    "houston_smoke": _real("houston_smoke", "2021-01-10", horizon_days=2),
}

SCENARIOS.update(REAL_SCENARIOS)
