"""Core domain types for MAHROS.

Everything in this module is plain-stdlib and hashable/serialisable so it can be
written to the audit ledger without extra machinery.
"""

from __future__ import annotations

import enum
import json
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any


class ResourceType(str, enum.Enum):
    """The escalation-relevant resources a hospital can run out of.

    Deliberately *not* generic "beds": the whole premise of MAHROS is that a
    patient is already admitted and stabilised, and now needs a level of care
    the current hospital cannot provide right now.
    """

    ICU_BED = "icu_bed"
    HDU_BED = "hdu_bed"          # high-dependency / step-down
    WARD_BED = "ward_bed"
    OR_SLOT = "or_slot"          # operating-room slot + surgical team
    VENTILATOR = "ventilator"
    CATH_LAB = "cath_lab"        # time-critical cardiac intervention

    @property
    def is_critical(self) -> bool:
        return self in (ResourceType.ICU_BED, ResourceType.VENTILATOR,
                        ResourceType.CATH_LAB, ResourceType.OR_SLOT)


class Specialty(str, enum.Enum):
    """Capability tags. A hospital may have a free ICU bed but no neurosurgeon."""

    GENERAL = "general"
    CARDIAC = "cardiac"
    NEURO = "neuro"
    TRAUMA = "trauma"
    PAEDIATRIC = "paediatric"
    OBSTETRIC = "obstetric"
    BURNS = "burns"


class Acuity(enum.IntEnum):
    """Clinical urgency. Higher = sicker = shorter safe transfer window."""

    ROUTINE = 1
    URGENT = 2
    EMERGENT = 3
    CRITICAL = 4
    LIFE_THREATENING = 5

    @property
    def safe_window_minutes(self) -> int:
        """Time from escalation to definitive care before outcome degrades.

        Calibrated against inter-hospital transfer reality, not in-hospital
        response times: the relevant benchmark is door-in-door-out plus
        transport, where a <60 min DIDO target and total transfer times of
        90-180 min are typical for time-critical cases. A 45-minute window for
        the sickest patients would be unachievable by *any* transfer system,
        road or otherwise, and would make every strategy look identically bad.

        These are modelling assumptions; sensitivity to them is tested in
        experiments/sensitivity.py and documented in docs/MODEL_ASSUMPTIONS.md.
        """
        return {1: 720, 2: 480, 3: 240, 4: 150, 5: 90}[int(self)]


class RequestStatus(str, enum.Enum):
    PENDING = "pending"          # created, not yet announced
    ANNOUNCED = "announced"      # call-for-proposals out, collecting bids
    AWARDED = "awarded"          # winner chosen, resource reserved
    IN_TRANSIT = "in_transit"
    COMPLETED = "completed"      # patient receiving definitive care
    FAILED = "failed"            # no feasible partner found
    EXPIRED = "expired"          # safe window blown before care delivered


class DemographicGroup(str, enum.Enum):
    """Coarse equity strata.

    Used only to *measure* fairness across patient populations; never used as a
    negotiation input (that would be discriminatory). See fairness/metrics.py.
    """

    URBAN_INSURED = "urban_insured"
    URBAN_UNINSURED = "urban_uninsured"
    RURAL_INSURED = "rural_insured"
    RURAL_UNINSURED = "rural_uninsured"


# --------------------------------------------------------------------------- #
# Patient / request
# --------------------------------------------------------------------------- #

def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


@dataclass
class Patient:
    """Internal-only record. Never crosses a hospital boundary un-anonymised."""

    patient_id: str = field(default_factory=lambda: _new_id("pat"))
    name: str = ""                       # synthetic; exists so the privacy layer has work to do
    mrn: str = ""                        # medical record number, hospital-local
    phone: str = ""
    age: int = 50
    group: DemographicGroup = DemographicGroup.URBAN_INSURED
    home_hospital: str = ""
    notes: str = ""                      # free text -> the hard case for PII scrubbing


@dataclass
class TransferRequest:
    """A call-for-proposals in Contract Net terms.

    This is the object that gets anonymised and broadcast to peer hospitals.
    """

    request_id: str = field(default_factory=lambda: _new_id("req"))
    origin: str = ""                     # hospital id that needs help
    patient_ref: str = ""                # pseudonymous token, NOT patient_id
    resource: ResourceType = ResourceType.ICU_BED
    specialty: Specialty = Specialty.GENERAL
    acuity: Acuity = Acuity.EMERGENT
    created_at: float = 0.0              # simulation minutes
    expires_at: float = 0.0              # created_at + acuity.safe_window_minutes
    expected_los_minutes: float = 2880.0  # how long the receiving resource is tied up
    group: DemographicGroup = DemographicGroup.URBAN_INSURED  # for equity audit only

    status: RequestStatus = RequestStatus.PENDING
    awarded_to: str | None = None
    awarded_at: float | None = None
    care_started_at: float | None = None
    failure_reason: str | None = None

    # bookkeeping for evaluation
    n_bids_received: int = 0
    n_peers_contacted: int = 0
    coordination_minutes: float = 0.0    # human/agent effort spent brokering

    @property
    def wait_minutes(self) -> float | None:
        """Escalation -> definitive care. The headline clinical metric."""
        if self.care_started_at is None:
            return None
        return self.care_started_at - self.created_at

    @property
    def breached(self) -> bool:
        if self.care_started_at is not None:
            return self.care_started_at > self.expires_at
        return self.status in (RequestStatus.FAILED, RequestStatus.EXPIRED)

    def public_view(self) -> dict[str, Any]:
        """Exactly the fields a peer hospital is allowed to see.

        Note what is absent: patient_id, name, mrn, age, and `group`. Peers bid
        on clinical need and nothing else.
        """
        return {
            "request_id": self.request_id,
            "origin": self.origin,
            "patient_ref": self.patient_ref,
            "resource": self.resource.value,
            "specialty": self.specialty.value,
            "acuity": int(self.acuity),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "expected_los_minutes": self.expected_los_minutes,
        }


# --------------------------------------------------------------------------- #
# Negotiation messages
# --------------------------------------------------------------------------- #

@dataclass
class Bid:
    """A peer hospital's proposal. Contract Net 'bid' message."""

    bid_id: str = field(default_factory=lambda: _new_id("bid"))
    request_id: str = ""
    bidder: str = ""
    feasible: bool = False
    refusal_reason: str = ""

    travel_minutes: float = 0.0          # origin -> bidder
    prep_minutes: float = 0.0            # bidder's readiness delay
    capability_match: float = 0.0        # 0..1, specialty/level fit
    post_accept_strain: float = 0.0      # bidder occupancy AFTER accepting, 0..1
    opportunity_cost: float = 0.0        # bidder's private valuation of giving the unit up
    submitted_at: float = 0.0

    @property
    def time_to_care(self) -> float:
        return self.travel_minutes + self.prep_minutes


@dataclass
class Agreement:
    """The immutable artefact written to the audit ledger."""

    agreement_id: str = field(default_factory=lambda: _new_id("agr"))
    request_id: str = ""
    origin: str = ""
    receiver: str = ""
    resource: ResourceType = ResourceType.ICU_BED
    # The clinical service agreed to. On the ledger because it is a *term of
    # the agreement*, and because a hospital that later claims it cannot treat
    # this specialty has to be checkable against what it has already accepted.
    specialty: Specialty = Specialty.GENERAL
    patient_ref: str = ""
    agreed_at: float = 0.0
    promised_care_start: float = 0.0
    terms_hash: str = ""
    decided_by: str = "cnp"              # cnp | llm_coordinator | fallback
    rationale: str = ""

    def canonical(self) -> str:
        """Deterministic serialisation -> what actually gets hashed on-chain."""
        payload = {
            "request_id": self.request_id,
            "origin": self.origin,
            "receiver": self.receiver,
            "resource": self.resource.value,
            "specialty": self.specialty.value,
            "patient_ref": self.patient_ref,
            "agreed_at": round(self.agreed_at, 4),
            "promised_care_start": round(self.promised_care_start, 4),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["resource"] = self.resource.value
        return d
