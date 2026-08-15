"""The simulation engine.

One run = one (scenario, strategy, seed) triple. The event loop is identical
across strategies; only `Strategy.resolve` differs, so results are comparable.

Patient lifecycle:

    ARRIVAL ---------> admitted locally (ward/HDU/ICU) or turned away
       |
       +-- with p(escalation) --> ESCALATION at t + delay
                                     |
                                     +-- local unit free?  -> treated in house
                                     +-- otherwise         -> TransferRequest
                                                                 |
                                                        strategy.resolve()
                                                                 |
                                              awarded -> travel -> CARE_START
                                              failed  -> retry until window expires
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from ..core.clock import Simulator
from ..core.types import (
    Acuity,
    Patient,
    RequestStatus,
    ResourceType,
    Specialty,
    TransferRequest,
)
from ..fairness.metrics import FairnessLedger, equity_report
from ..hospital.hospital import Hospital, HospitalConfig
from ..ledger.interface import HashChainLedger, LedgerBackend
from ..llm.coordinator import LLMCoordinator
from ..negotiation.cnp import CNPConfig
from ..negotiation.messages import MessageBus
from ..negotiation.scoring import ScoringWeights
from ..privacy.anonymizer import PrivacyAudit, build_anonymizer
from .scenario import (
    PatientGenerator,
    ScenarioConfig,
    build_network,
    travel_time_matrix,
)
from .strategies import STRATEGIES, StrategyContext, StrategyResult

# Events
ARRIVAL = "arrival"
ESCALATION = "escalation"
DISCHARGE = "discharge"
NEGOTIATE = "negotiate"
CARE_START = "care_start"
EXPIRE = "expire"
SAMPLE = "sample"


@dataclass
class RunConfig:
    scenario: ScenarioConfig
    strategy: str = "mahros"
    seed: int = 42
    fairness_enabled: bool = True
    llm_enabled: bool = False
    llm_arbitration: bool = False
    audit_enabled: bool = True
    use_presidio: bool = False
    retry_interval_minutes: float = 20.0   # re-attempt a failed transfer
    sample_interval_minutes: float = 30.0


@dataclass
class RunResult:
    config: RunConfig
    requests: list[TransferRequest] = field(default_factory=list)
    agreements: list[Any] = field(default_factory=list)
    hospitals: dict[str, Hospital] = field(default_factory=dict)
    fairness: FairnessLedger | None = None
    ledger: LedgerBackend | None = None
    bus: MessageBus | None = None
    privacy: PrivacyAudit | None = None
    coordinator: LLMCoordinator | None = None
    strategy_stats: dict = field(default_factory=dict)
    total_admissions: int = 0
    rejected_admissions: int = 0
    wall_seconds: float = 0.0


class SimulationRunner:
    def __init__(self, cfg: RunConfig) -> None:
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)
        self.scenario = cfg.scenario

        # -- network -------------------------------------------------------- #
        hospital_cfgs: list[HospitalConfig] = build_network(self.scenario)
        self.sim = Simulator(self.scenario.horizon_hours * 60.0)
        self.hospitals: dict[str, Hospital] = {
            hc.hospital_id: Hospital(hc, self.sim) for hc in hospital_cfgs
        }
        self._tt = travel_time_matrix(hospital_cfgs, self.scenario.ambulance_speed_kmh)

        # -- layers --------------------------------------------------------- #
        weights = {
            h.hospital_id: float(sum(
                n for r, n in h.capacities.items() if r.is_critical
            )) for h in hospital_cfgs
        }
        self.fairness = FairnessLedger(
            list(self.hospitals), window=200, capacity_weights=weights
        )
        self.privacy = PrivacyAudit(build_anonymizer(prefer_presidio=cfg.use_presidio))
        self.bus = MessageBus()
        self.ledger: LedgerBackend = HashChainLedger() if cfg.audit_enabled else _null_ledger()
        self.coordinator = LLMCoordinator() if cfg.llm_enabled else None

        self.ctx = StrategyContext(
            hospitals=self.hospitals,
            travel_time=self.travel_time,
            fairness=self.fairness,
            privacy=self.privacy,
            bus=self.bus,
            ledger=self.ledger,
            weights=ScoringWeights(fairness_enabled=cfg.fairness_enabled),
            cnp=CNPConfig(enable_llm_arbitration=cfg.llm_arbitration),
            coordinator=self.coordinator,
            rng=random.Random(cfg.seed + 555),
        )
        self.strategy = STRATEGIES[cfg.strategy](self.ctx)

        self.gen = PatientGenerator(self.scenario)
        self.requests: list[TransferRequest] = []
        self.agreements: list[Any] = []
        self.total_admissions = 0
        self.rejected_admissions = 0

        self._wire_handlers()

    # -- helpers ----------------------------------------------------------- #
    def travel_time(self, a: str, b: str) -> float:
        return self._tt.get((a, b), 999.0)

    def _wire_handlers(self) -> None:
        self.sim.on(ARRIVAL, self._on_arrival)
        self.sim.on(ESCALATION, self._on_escalation)
        self.sim.on(DISCHARGE, self._on_discharge)
        self.sim.on(NEGOTIATE, self._on_negotiate)
        self.sim.on(CARE_START, self._on_care_start)
        self.sim.on(EXPIRE, self._on_expire)
        self.sim.on(SAMPLE, self._on_sample)

    # -- event handlers ---------------------------------------------------- #
    def _on_arrival(self, now: float, payload: dict) -> None:
        hid = payload["hospital"]
        hosp = self.hospitals[hid]

        # schedule the next arrival at this hospital
        gap = self.gen.arrival_gap(hosp.cfg.base_arrival_rate, now)
        self.sim.schedule(gap, ARRIVAL, {"hospital": hid})

        patient = self.gen.make_patient(hid)
        resource, los = self.gen.initial_admission()
        self.total_admissions += 1

        if not hosp.admit_local(resource):
            # No bed at all: outside MAHROS's scope (that is a diversion problem),
            # counted so the network's baseline strain is visible.
            self.rejected_admissions += 1
            return

        self.sim.schedule(los, DISCHARGE, {"hospital": hid, "resource": resource})

        if self.rng.random() < hosp.cfg.escalation_prob:
            self.sim.schedule(
                self.gen.escalation_delay(),
                ESCALATION,
                {"hospital": hid, "patient": patient, "held": resource},
            )

    def _on_escalation(self, now: float, payload: dict) -> None:
        """The core event: an admitted patient now needs higher-level care."""
        hid = payload["hospital"]
        hosp = self.hospitals[hid]
        patient: Patient = payload["patient"]

        resource, specialty, acuity, los = self.gen.escalation_profile()

        # Can the hospital handle the escalation itself? If so, no transfer.
        if hosp.can_admit_locally(resource, specialty):
            if hosp.admit_local(resource):
                self.sim.schedule(los, DISCHARGE, {"hospital": hid, "resource": resource})
                return

        token = self.privacy.anonymizer.pseudonymize(patient, hid)
        req = TransferRequest(
            origin=hid,
            patient_ref=token,
            resource=resource,
            specialty=specialty,
            acuity=acuity,
            created_at=now,
            expires_at=now + acuity.safe_window_minutes,
            expected_los_minutes=los,
            group=patient.group,
        )
        self.requests.append(req)
        hosp.sent_requests.append(req)
        hosp.sent_count += 1

        self.sim.schedule(0.0, NEGOTIATE, {"request": req, "attempt": 1})
        self.sim.schedule_at(req.expires_at, EXPIRE, {"request": req})

    def _on_negotiate(self, now: float, payload: dict) -> None:
        req: TransferRequest = payload["request"]
        if req.status in (RequestStatus.AWARDED, RequestStatus.COMPLETED,
                          RequestStatus.EXPIRED, RequestStatus.IN_TRANSIT):
            return
        if now >= req.expires_at:
            return

        result: StrategyResult = self.strategy.resolve(req, now)

        if result.agreement is not None or result.receiver is not None:
            req.status = RequestStatus.IN_TRANSIT
            req.awarded_to = result.receiver
            req.awarded_at = now + result.elapsed_minutes
            if result.agreement is not None:
                self.agreements.append(result.agreement)
                care_at = result.agreement.promised_care_start
            else:
                care_at = now + result.elapsed_minutes  # local hold, no travel
            self.sim.schedule_at(care_at, CARE_START, {"request": req})
            return

        # Failed this attempt. Retry while clinical time remains -- capacity
        # frees up continuously, so a retry is realistic, not a fudge.
        req.failure_reason = result.failure_reason
        retry_at = now + result.elapsed_minutes + self.cfg.retry_interval_minutes
        if retry_at < req.expires_at:
            self.sim.schedule_at(
                retry_at, NEGOTIATE,
                {"request": req, "attempt": payload["attempt"] + 1},
            )

    def _on_care_start(self, now: float, payload: dict) -> None:
        req: TransferRequest = payload["request"]
        if req.status is RequestStatus.EXPIRED:
            return
        receiver = self.hospitals.get(req.awarded_to or "")
        if receiver is None:
            return

        if receiver.receive_patient(req):
            req.status = RequestStatus.COMPLETED
            req.care_started_at = now
            self.sim.schedule(
                req.expected_los_minutes, DISCHARGE,
                {"hospital": receiver.id, "resource": req.resource},
            )
        else:
            # Reservation lapsed before arrival. Renegotiate if time allows.
            req.status = RequestStatus.PENDING
            req.awarded_to = None
            if now + 5 < req.expires_at:
                self.sim.schedule(5.0, NEGOTIATE, {"request": req, "attempt": 99})

    def _on_expire(self, now: float, payload: dict) -> None:
        req: TransferRequest = payload["request"]
        if req.status in (RequestStatus.COMPLETED, RequestStatus.IN_TRANSIT):
            return
        req.status = RequestStatus.EXPIRED
        if req.awarded_to:
            self.hospitals[req.awarded_to].release(req)

    def _on_discharge(self, now: float, payload: dict) -> None:
        self.hospitals[payload["hospital"]].discharge(payload["resource"])

    def _on_sample(self, now: float, _payload) -> None:
        for h in self.hospitals.values():
            h.resources.sample_utilisation()
            for pool in h.resources.pools.values():
                for rid in pool.expire_holds(now):
                    pass  # hold lapsed; the CARE_START handler will renegotiate
        self.sim.schedule(self.cfg.sample_interval_minutes, SAMPLE, None)

    # -- entry point ------------------------------------------------------- #
    def run(self) -> RunResult:
        import time
        t0 = time.perf_counter()

        for hid, hosp in self.hospitals.items():
            self.sim.schedule(
                self.gen.arrival_gap(hosp.cfg.base_arrival_rate, 0.0),
                ARRIVAL, {"hospital": hid},
            )
        self.sim.schedule(self.cfg.sample_interval_minutes, SAMPLE, None)
        self.sim.run()

        if hasattr(self.ledger, "flush"):
            self.ledger.flush()

        return RunResult(
            config=self.cfg,
            requests=self.requests,
            agreements=self.agreements,
            hospitals=self.hospitals,
            fairness=self.fairness,
            ledger=self.ledger,
            bus=self.bus,
            privacy=self.privacy,
            coordinator=self.coordinator,
            strategy_stats=self.strategy.stats(),
            total_admissions=self.total_admissions,
            rejected_admissions=self.rejected_admissions,
            wall_seconds=time.perf_counter() - t0,
        )


def _null_ledger() -> LedgerBackend:
    from ..ledger.interface import NullLedger
    return NullLedger()
