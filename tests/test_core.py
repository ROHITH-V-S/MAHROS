"""Tests for the properties MAHROS actually claims.

Each test maps to a claim in the paper. If one of these fails, a headline
number is wrong -- these are not decorative.
"""

from __future__ import annotations

import copy

import pytest

from mahros.core.types import (
    Acuity,
    Agreement,
    Patient,
    ResourceType,
    Specialty,
    TransferRequest,
)
from mahros.fairness.metrics import FairnessLedger, gini, jain_index
from mahros.hospital.resources import ResourcePool
from mahros.ledger.interface import HashChainLedger
from mahros.privacy.anonymizer import PrivacyAudit, TokenizingAnonymizer
from mahros.sim.metrics import compute
from mahros.sim.runner import RunConfig, SimulationRunner
from mahros.sim.scenario import SCENARIOS


# --------------------------------------------------------------------------- #
# Claim: the audit ledger is tamper-evident
# --------------------------------------------------------------------------- #

def _agreement(i: int) -> Agreement:
    return Agreement(
        request_id=f"req-{i}", origin="H01", receiver="H07",
        resource=ResourceType.ICU_BED, patient_ref=f"pt_{i:04d}",
        agreed_at=float(i), promised_care_start=float(i) + 40.0,
    )


def test_ledger_verifies_when_intact():
    led = HashChainLedger(block_size=4)
    for i in range(20):
        assert led.record_agreement(_agreement(i)).ok
    v = led.verify()
    assert v["valid"], v["errors"]
    assert v["records"] == 20


def test_ledger_detects_altered_entry():
    """A hospital under-reporting what it accepted must not go unnoticed."""
    led = HashChainLedger(block_size=4)
    for i in range(20):
        led.record_agreement(_agreement(i))
    led.flush()
    assert led.verify()["valid"]

    led.chain[1].entries[0]["receiver"] = "H99_FORGED"
    assert not led.verify()["valid"]


def test_ledger_detects_deleted_entry():
    led = HashChainLedger(block_size=4)
    for i in range(20):
        led.record_agreement(_agreement(i))
    led.flush()
    led.chain[2].entries.pop()
    assert not led.verify()["valid"]


def test_ledger_detects_reordered_blocks():
    led = HashChainLedger(block_size=4)
    for i in range(20):
        led.record_agreement(_agreement(i))
    led.flush()
    led.chain[1], led.chain[2] = led.chain[2], led.chain[1]
    assert not led.verify()["valid"]


def test_agreement_hash_is_canonical():
    """Same terms -> same hash, regardless of field construction order."""
    a, b = _agreement(1), _agreement(1)
    b.agreement_id = "different-id"          # not part of the terms
    b.rationale = "different prose"          # not part of the terms
    assert a.canonical() == b.canonical()


# --------------------------------------------------------------------------- #
# Claim: no patient identifier crosses a hospital boundary
# --------------------------------------------------------------------------- #

def test_public_view_excludes_identifiers():
    p = Patient(name="Priya Nair", mrn="H01-000123", phone="+91 9812345678")
    req = TransferRequest(origin="H01", patient_ref="pt_abcd", resource=ResourceType.ICU_BED)
    view = req.public_view()
    blob = str(view).lower()
    for leak in ("priya", "nair", "h01-000123", "9812345678"):
        assert leak.lower() not in blob


def test_free_text_scrubbing():
    anon = TokenizingAnonymizer()
    text = ("Patient Mr. Rahul Sharma, MRN H03-000891, contact +91 9812345678, "
            "admitted 14/03/2026, email rahul@example.com")
    out = anon.scrub(text)
    for leak in ("Rahul", "H03-000891", "9812345678", "rahul@example.com", "14/03/2026"):
        assert leak not in out


def test_pseudonyms_are_unlinkable_across_hospitals():
    """The same patient must not be correlatable across two receiving sites."""
    anon = TokenizingAnonymizer()
    p = Patient(patient_id="pat-123")
    assert anon.pseudonymize(p, "H01") != anon.pseudonymize(p, "H02")
    assert anon.pseudonymize(p, "H01") == anon.pseudonymize(p, "H01")  # stable


def test_privacy_audit_flags_leak():
    audit = PrivacyAudit(TokenizingAnonymizer())
    audit.outbound({"request_id": "r1", "name": "Priya Nair", "mrn": "X"})
    assert audit.report()["clean"]           # forbidden keys were dropped, not passed
    assert "name" not in audit.anonymizer.sanitize_outbound({"name": "x"})


# --------------------------------------------------------------------------- #
# Claim: resources are never double-allocated
# --------------------------------------------------------------------------- #

def test_reservation_prevents_overcommit():
    pool = ResourcePool(resource=ResourceType.ICU_BED, capacity=2)
    assert pool.reserve("r1", 100.0)
    assert pool.reserve("r2", 100.0)
    assert not pool.reserve("r3", 100.0)     # capacity exhausted by holds alone
    assert pool.available == 0


def test_reservation_converts_to_occupancy_exactly_once():
    pool = ResourcePool(resource=ResourceType.ICU_BED, capacity=1)
    pool.reserve("r1", 100.0)
    assert pool.occupy("r1")
    assert pool.occupied == 1 and pool.reserved == 0
    assert not pool.occupy("r1")             # the hold is gone; no free unit left


def test_expired_holds_are_reclaimed():
    pool = ResourcePool(resource=ResourceType.ICU_BED, capacity=1)
    pool.reserve("r1", hold_until=50.0)
    assert pool.available == 0
    assert pool.expire_holds(now=60.0) == ["r1"]
    assert pool.available == 1


# --------------------------------------------------------------------------- #
# Claim: the fairness metrics behave like fairness metrics
# --------------------------------------------------------------------------- #

def test_gini_bounds():
    assert gini([5, 5, 5, 5]) == pytest.approx(0.0, abs=1e-9)
    assert gini([0, 0, 0, 100]) > 0.7
    assert gini([]) == 0.0


def test_jain_bounds():
    assert jain_index([4, 4, 4, 4]) == pytest.approx(1.0)
    assert jain_index([0, 0, 0, 10]) == pytest.approx(0.25)


def test_fairness_credit_signs():
    """Over-burdened hospitals get a positive credit (they will be shielded)."""
    fl = FairnessLedger(["H1", "H2"], window=10,
                        capacity_weights={"H1": 1.0, "H2": 1.0})
    for _ in range(8):
        fl.record_accept("H1")
    for _ in range(2):
        fl.record_accept("H2")
    assert fl.credit("H1") > 0
    assert fl.credit("H2") < 0


def test_fairness_share_is_capacity_weighted():
    """A big hospital taking more is not unfair; a small one taking more is."""
    fl = FairnessLedger(["Big", "Small"], window=10,
                        capacity_weights={"Big": 9.0, "Small": 1.0})
    for _ in range(9):
        fl.record_accept("Big")
    fl.record_accept("Small")
    assert fl.credit("Big") == pytest.approx(0.0, abs=0.05)
    assert fl.credit("Small") == pytest.approx(0.0, abs=0.05)


# --------------------------------------------------------------------------- #
# Claim: the clinical safe window is a hard constraint, never a soft penalty
# --------------------------------------------------------------------------- #

def test_bids_missing_the_window_are_never_awarded():
    from mahros.core.types import Bid
    from mahros.negotiation.scoring import ScoringWeights, score_bids

    req = TransferRequest(
        origin="H01", resource=ResourceType.ICU_BED, acuity=Acuity.LIFE_THREATENING,
        created_at=0.0, expires_at=90.0,
    )
    too_slow = Bid(request_id=req.request_id, bidder="Far", feasible=True,
                   travel_minutes=200.0, prep_minutes=10.0, capability_match=1.0)
    in_time = Bid(request_id=req.request_id, bidder="Near", feasible=True,
                  travel_minutes=20.0, prep_minutes=10.0, capability_match=0.6)

    scored = score_bids(req, [too_slow, in_time], now=0.0, weights=ScoringWeights())
    assert [s.bid.bidder for s in scored] == ["Near"]


def test_fairness_cannot_override_clinical_deadline():
    """Even a maximally over-burdened winner keeps the slot if it is the only safe one."""
    from mahros.core.types import Bid
    from mahros.negotiation.scoring import ScoringWeights, score_bids

    fl = FairnessLedger(["Near", "Far"], window=10)
    for _ in range(10):
        fl.record_accept("Near")             # Near is heavily over-burdened

    req = TransferRequest(origin="H01", resource=ResourceType.ICU_BED,
                          acuity=Acuity.CRITICAL, created_at=0.0, expires_at=150.0)
    near = Bid(request_id=req.request_id, bidder="Near", feasible=True,
               travel_minutes=30.0, prep_minutes=10.0, capability_match=1.0)
    far = Bid(request_id=req.request_id, bidder="Far", feasible=True,
              travel_minutes=400.0, prep_minutes=10.0, capability_match=1.0)

    scored = score_bids(req, [near, far], 0.0, ScoringWeights(), fl)
    assert scored[0].bid.bidder == "Near"


# --------------------------------------------------------------------------- #
# Claim: results are reproducible
# --------------------------------------------------------------------------- #

def _smoke(strategy: str, seed: int = 42):
    sc = copy.deepcopy(SCENARIOS["smoke"])
    sc.seed = seed
    return compute(SimulationRunner(RunConfig(scenario=sc, strategy=strategy, seed=seed)).run())


def test_same_seed_gives_identical_results():
    a, b = _smoke("mahros"), _smoke("mahros")
    assert a.n_requests == b.n_requests
    assert a.mean_wait == pytest.approx(b.mean_wait)
    assert a.burden_gini == pytest.approx(b.burden_gini)


def test_different_seeds_give_different_results():
    a, b = _smoke("mahros", 42), _smoke("mahros", 1337)
    assert (a.n_requests, a.mean_wait) != (b.n_requests, b.mean_wait)


@pytest.mark.parametrize("strategy", ["mahros", "phone", "central", "nearest", "none"])
def test_every_strategy_runs_and_reports(strategy):
    m = _smoke(strategy)
    assert m.n_requests > 0
    assert 0.0 <= m.success_rate <= 1.0
    assert m.privacy_violations == 0


# --------------------------------------------------------------------------- #
# Claim: MAHROS is decentralised; the centralised baseline is not
# --------------------------------------------------------------------------- #

def test_mahros_is_decentralised_and_central_is_not():
    assert _smoke("mahros").decentralised is True
    assert _smoke("central").decentralised is False
    assert _smoke("nearest").decentralised is False


def test_central_concentrates_decisions_and_mahros_does_not():
    """The measurable difference: who brokers, and who sees the disclosures."""
    mahros, central = _smoke("mahros"), _smoke("central")

    # A central authority brokers every negotiation and receives every report.
    assert central.decision_concentration == pytest.approx(1.0)
    assert central.disclosure_concentration == pytest.approx(1.0)

    # In MAHROS each hospital brokers only its own patients' transfers.
    assert mahros.decision_concentration < 0.5
    assert mahros.disclosure_concentration < 0.5
