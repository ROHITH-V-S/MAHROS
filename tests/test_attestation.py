"""Tests for attested refusal.

Two properties matter more than the rest and are tested hardest:

* **Nothing about capacity is disclosed.** The attestation must carry the
  contested predicate and no quantity. If a bed count ever appears in a signed
  payload, the design has quietly become the central capacity registry it exists
  to avoid.
* **Referrals never overrule anyone.** Audit referrals have a non-zero false
  positive rate by construction. They are advisory, retrospective, and must stay
  out of the live decision path, or the zero-false-accusation property collapses.
"""

from __future__ import annotations

import copy
import random

import pytest

from mahros.core.types import Agreement, ResourceType, Specialty
from mahros.hospital.behaviours import (
    DefensivePolicy,
    HonestPolicy,
    ReportedAssessment,
    StrategicPolicy,
)
from mahros.ledger.interface import HashChainLedger
from mahros.negotiation.attestation import (
    AUDIT_REFERRAL_WINDOW_MINUTES,
    NO_UNIT_AVAILABLE,
    AttestationAuthority,
    CapacityAttestation,
    Keyring,
    provides_non_repudiation,
    signer_backend,
)
from mahros.sim import metrics as M
from mahros.sim.runner import RunConfig, SimulationRunner
from mahros.sim.scenario import SCENARIOS


@pytest.fixture
def auth():
    return AttestationAuthority(Keyring(seed=b"test"))


def _short_run(**kw):
    sc = copy.deepcopy(SCENARIOS["houston_surge"])
    sc.horizon_hours = 24 * 3
    sc.seed = 42
    kw.setdefault("strategic_fraction", 0.5)
    result = SimulationRunner(RunConfig(
        scenario=sc, strategy="mahros", seed=42, **kw)).run()
    return result, M.compute(result)


# --------------------------------------------------------------------------- #
# Signatures
# --------------------------------------------------------------------------- #

def test_backend_is_reported_not_assumed():
    assert signer_backend() in ("ed25519", "hmac-sha256")
    # The honest claim: non-repudiation only with asymmetric signatures.
    assert provides_non_repudiation() == (signer_backend() == "ed25519")


def test_issued_attestation_verifies(auth):
    att = auth.attest("450068", "req-1", "icu_bed", 100.0)
    assert auth.verify(att)
    assert att.hospital_signature and att.cosigner_signature


def test_tampering_with_any_field_breaks_verification(auth):
    att = auth.attest("450068", "req-1", "icu_bed", 100.0)
    for field, value in (
        ("hospital", "450358"),
        ("request_id", "req-2"),
        ("resource", "ward_bed"),
        ("at", 200.0),
        ("nonce", "deadbeef"),
        ("asserted", "something_else"),
    ):
        forged = CapacityAttestation(**{**att.__dict__, field: value})
        assert not auth.verify(forged), f"tampered {field} still verified"


def test_a_hospital_cannot_forge_the_cosignature(auth):
    """The co-signer is the party a hospital does not control."""
    att = auth.attest("450068", "req-1", "icu_bed", 100.0)
    solo = CapacityAttestation(**{**att.__dict__, "cosigner_signature": ""})
    assert not auth.verify(solo)
    # Nor can it reuse its own signature in the co-signer slot.
    swapped = CapacityAttestation(
        **{**att.__dict__, "cosigner_signature": att.hospital_signature})
    assert not auth.verify(swapped)


def test_forgery_is_counted(auth):
    att = auth.attest("450068", "req-1", "icu_bed", 100.0)
    bad = CapacityAttestation(**{**att.__dict__, "hospital_signature": "00" * 32})
    auth.verify(bad)
    assert auth.stats()["forged_rejected"] >= 1


# --------------------------------------------------------------------------- #
# The privacy property: the predicate, never the quantity
# --------------------------------------------------------------------------- #

def test_attestation_discloses_no_capacity_quantity(auth):
    """The single most important property in this module.

    A signed payload that carried a bed count would make every challenged
    hospital disclose its occupancy -- rebuilding the central registry the whole
    design avoids.
    """
    import json as _json

    att = auth.attest("450068", "req-1", "icu_bed", 100.0)
    signed = _json.loads(att.canonical().decode())

    # No field may name a capacity quantity. ("available" is deliberately not
    # in this list: it appears inside the predicate NO_UNIT_AVAILABLE, which is
    # the contested proposition itself, not a disclosure about state.)
    for banned in ("occupied", "capacity", "free", "beds", "census",
                   "occupancy", "reserved", "count", "available_units"):
        assert banned not in att.__dict__, f"{banned} is a field"
        assert banned not in signed, f"{banned} is in the signed payload"

    # The only number in the signed payload is the timestamp. Anything else
    # numeric would be a quantity, and a quantity is a disclosure.
    numeric = {k: v for k, v in signed.items() if isinstance(v, (int, float))}
    assert set(numeric) == {"at"}, f"unexpected quantity in payload: {numeric}"
    assert att.asserted == NO_UNIT_AVAILABLE


def test_attestation_asserts_only_the_contested_predicate(auth):
    att = auth.attest("450068", "req-1", "icu_bed", 100.0)
    # Exactly the proposition the refusal already made in public.
    assert att.asserted == NO_UNIT_AVAILABLE
    assert set(att.as_dict()) >= {"hospital", "resource", "asserted", "digest"}
    assert "occupied" not in att.as_dict()


def test_public_record_states_whether_non_repudiation_is_real(auth):
    att = auth.attest("450068", "req-1", "icu_bed", 100.0)
    assert att.as_dict()["non_repudiation"] == provides_non_repudiation()


# --------------------------------------------------------------------------- #
# Who signs, and who does not
# --------------------------------------------------------------------------- #

def _reported(misreported: bool):
    class _T:
        feasible = not misreported
    return ReportedAssessment(reported=_T(), truthful=_T(),
                              misreported=misreported)


def test_honest_hospital_always_signs():
    rng = random.Random(0)
    policy = HonestPolicy()
    for deterrence in (0.0, 0.5, 1.0):
        assert policy.will_attest(_reported(False), rng, deterrence)


def test_cautious_hospital_always_signs():
    """A defensive hospital is telling the truth, so signing costs it nothing."""
    rng = random.Random(0)
    assert DefensivePolicy().will_attest(_reported(False), rng, 1.0)


def test_liar_declines_in_proportion_to_deterrence():
    policy = StrategicPolicy()
    for deterrence in (0.0, 0.25, 0.5, 0.75, 1.0):
        rng = random.Random(7)
        declined = sum(
            0 if policy.will_attest(_reported(True), rng, deterrence) else 1
            for _ in range(4000))
        assert abs(declined / 4000 - deterrence) < 0.05, deterrence


def test_liar_still_signs_a_claim_that_is_true():
    """A strategic hospital is not lying on every request."""
    rng = random.Random(0)
    assert StrategicPolicy().will_attest(_reported(False), rng, 1.0)


# --------------------------------------------------------------------------- #
# Audit referrals are advisory, and say so
# --------------------------------------------------------------------------- #

def test_referral_is_not_labelled_a_finding(auth):
    ledger = HashChainLedger(block_size=4)
    att = auth.attest("450068", "req-1", "icu_bed", 100.0)
    ledger.record_agreement(Agreement(
        request_id="req-2", origin="450358", receiver="450068",
        resource=ResourceType.ICU_BED, specialty=Specialty.GENERAL,
        patient_ref="p1", agreed_at=102.0, decided_by="test"))
    refs = auth.audit(ledger, now=200.0)
    assert len(refs) == 1
    d = refs[0].as_dict()
    assert "NOT a finding" in d["status"]
    assert "false_positive_rate" in d
    # The wording must leave room for the innocent explanation.
    assert "discharge" in refs[0].claim


def test_referral_ignores_acceptances_outside_the_window(auth):
    ledger = HashChainLedger(block_size=4)
    auth.attest("450068", "req-1", "icu_bed", 100.0)
    ledger.record_agreement(Agreement(
        request_id="req-2", origin="450358", receiver="450068",
        resource=ResourceType.ICU_BED, specialty=Specialty.GENERAL,
        patient_ref="p1",
        agreed_at=100.0 + AUDIT_REFERRAL_WINDOW_MINUTES + 30.0,
        decided_by="test"))
    assert auth.audit(ledger, now=500.0) == []


def test_referral_ignores_the_same_request(auth):
    """Being awarded the patient you refused is not evidence against you."""
    ledger = HashChainLedger(block_size=4)
    auth.attest("450068", "req-1", "icu_bed", 100.0)
    ledger.record_agreement(Agreement(
        request_id="req-1", origin="450358", receiver="450068",
        resource=ResourceType.ICU_BED, specialty=Specialty.GENERAL,
        patient_ref="p1", agreed_at=101.0, decided_by="test"))
    assert auth.audit(ledger, now=200.0) == []


# --------------------------------------------------------------------------- #
# End to end
# --------------------------------------------------------------------------- #

def test_attestation_is_off_by_default():
    result, _ = _short_run()
    assert result.attestation is None, (
        "every pre-existing result must be the unattested protocol")


def test_full_deterrence_reproduces_the_unattested_protocol():
    """At deterrence 1.0 the old optimistic assumption is exactly recovered."""
    _, base = _short_run()
    _, attested = _short_run(enable_attestation=True,
                             attestation_deterrence=1.0)
    assert abs(attested.success_rate - base.success_rate) < 0.03


def test_zero_deterrence_collapses_toward_plain_contract_net():
    _, plain = _short_run(enable_argumentation=False)
    _, attested = _short_run(enable_attestation=True,
                             attestation_deterrence=0.0)
    _, base = _short_run()
    assert attested.success_rate < base.success_rate
    assert abs(attested.success_rate - plain.success_rate) < 0.05


def test_live_mechanism_keeps_zero_false_accusations_under_attestation():
    """Referrals must not leak into the overruling path."""
    for deterrence in (0.0, 0.5, 1.0):
        result, _ = _short_run(enable_attestation=True,
                               attestation_deterrence=deterrence,
                               defensive_fraction=0.25,
                               strategic_fraction=0.25)
        wrongly = sum(h.refusals_overruled for h in result.hospitals.values()
                      if h.policy.name != "strategic")
        assert wrongly == 0, f"false accusation at deterrence {deterrence}"


def test_signatures_are_cheap_enough_to_be_live(auth):
    """Verification sits inside a live negotiation, so it must be microseconds."""
    import time
    atts = [auth.attest("450068", f"r{i}", "icu_bed", float(i))
            for i in range(2000)]
    t0 = time.perf_counter()
    for att in atts:
        auth.verify(att)
    per_us = (time.perf_counter() - t0) / len(atts) * 1e6
    assert per_us < 500, f"{per_us:.0f} us per verification is too slow"
