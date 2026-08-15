"""Synthetic hospital-network generator.

Everything here is a documented modelling assumption, not a claim about a real
health system. Parameters are collected in one place so `docs/MODEL_ASSUMPTIONS.md`
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
from ..hospital.hospital import HospitalConfig


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
    configs: list[HospitalConfig], speed_kmh: float
) -> dict[tuple[str, str], float]:
    """Euclidean distance -> minutes, with a fixed load/handover overhead.

    The 12-minute constant covers packaging the patient, crew handover at both
    ends, and is why very short hops are not free.
    """
    out: dict[tuple[str, str], float] = {}
    for a in configs:
        for b in configs:
            if a.hospital_id == b.hospital_id:
                out[(a.hospital_id, b.hospital_id)] = 0.0
                continue
            d = math.hypot(a.x - b.x, a.y - b.y)
            out[(a.hospital_id, b.hospital_id)] = 12.0 + (d / speed_kmh) * 60.0
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
        return resource, specialty, acuity, los

    #: initial admission mix -> (resource, weight, mean LOS minutes)
    _ADMISSION_MIX = [
        (ResourceType.WARD_BED, 0.900, 2600.0),   # ~43 h
        (ResourceType.HDU_BED,  0.075, 1800.0),   # ~30 h
        (ResourceType.ICU_BED,  0.025, 2400.0),   # ~40 h
    ]

    def initial_admission(self) -> tuple[ResourceType, float]:
        """Most admissions start on a ward bed and stay a couple of days.

        Critical-care beds are kept lightly loaded *at admission* on purpose:
        the escalation stream is what fills them, and if routine admissions
        already saturated the ICU there would be no transfer problem to study,
        only a capacity problem.
        """
        resource, _, mean_los = self.rng.choices(
            self._ADMISSION_MIX, weights=[w for _, w, _ in self._ADMISSION_MIX], k=1
        )[0]
        los = max(120.0, self.rng.lognormvariate(math.log(mean_los), 0.6))
        return resource, los

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
