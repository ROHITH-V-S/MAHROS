"""The ledger's read indices must agree with a full scan, exactly.

`accepted_by`, `refusals_by` and `broken_commitments` are the evidence base a
challenge is built from, so their results decide who gets overruled. They were
made fast by indexing writes instead of rescanning the whole chain; these tests
pin that the fast path returns precisely what the slow path did, including the
not-yet-sealed pending entries.

A performance change to this code must not move a single result. If it does,
the argumentation layer is deciding differently and every adversarial number in
the paper is affected.
"""

from __future__ import annotations

import random

import pytest

from mahros.core.types import Agreement, ResourceType, Specialty
from mahros.ledger.interface import HashChainLedger

HOSPITALS = [f"H{i:02d}" for i in range(6)]
RESOURCES = list(ResourceType)
SPECIALTIES = list(Specialty)


def _reference_accepted_by(ledger, hospital, since=None, resource=None,
                           specialty=None):
    """The original implementation: scan the entire history and filter."""
    rows = [
        e for e in ledger.history()
        if e.get("receiver") == hospital
        and (since is None or e.get("agreed_at", 0.0) >= since)
        and (resource is None or e.get("resource") == resource)
        and (specialty is None or e.get("specialty") == specialty)
    ]
    rows.sort(key=lambda e: e.get("agreed_at", 0.0), reverse=True)
    return rows


def _reference_refusals_by(ledger, hospital, since=None, resource=None):
    rows = [
        e for e in ledger.history()
        if e.get("kind") == "refusal"
        and e.get("hospital") == hospital
        and (since is None or e.get("agreed_at", 0.0) >= since)
        and (resource is None or e.get("resource") == resource)
    ]
    rows.sort(key=lambda e: e.get("agreed_at", 0.0), reverse=True)
    return rows


def _reference_broken(ledger, hospital, window_minutes=60.0, since=None):
    refusals = _reference_refusals_by(ledger, hospital, since=since)
    accepts = [e for e in ledger.history()
               if e.get("kind") != "refusal" and e.get("receiver") == hospital]
    pairs = []
    for refusal in refusals:
        t0 = refusal.get("agreed_at", 0.0)
        for accept in accepts:
            t1 = accept.get("agreed_at", 0.0)
            if (accept.get("resource") == refusal.get("resource")
                    and t0 < t1 <= t0 + window_minutes
                    and accept.get("request_id") != refusal.get("request_id")):
                pairs.append((refusal, accept))
                break
    return pairs


def _populate(seed: int, n: int = 400, block_size: int = 16):
    """A ledger with interleaved agreements and refusals in event-time order."""
    rng = random.Random(seed)
    ledger = HashChainLedger(block_size=block_size)
    now = 0.0
    for i in range(n):
        now += rng.expovariate(1 / 7.0)
        hospital = rng.choice(HOSPITALS)
        resource = rng.choice(RESOURCES)
        if rng.random() < 0.45:
            ledger.record_refusal(
                hospital=hospital, resource=resource.value,
                request_id=f"R{i:05d}", reason="at_self_protection_reserve",
                at=now)
        else:
            ledger.record_agreement(Agreement(
                request_id=f"R{i:05d}",
                origin=rng.choice(HOSPITALS),
                receiver=hospital,
                resource=resource,
                specialty=rng.choice(SPECIALTIES),
                patient_ref=f"P{i:05d}",
                agreed_at=now,
                decided_by="test",
            ))
    return ledger, now


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_accepted_by_matches_full_scan(seed):
    ledger, end = _populate(seed)
    for hospital in HOSPITALS + ["NOT_A_HOSPITAL"]:
        assert ledger.accepted_by(hospital) == \
            _reference_accepted_by(ledger, hospital)
        for since in (None, 0.0, end / 2, end * 2):
            assert ledger.accepted_by(hospital, since=since) == \
                _reference_accepted_by(ledger, hospital, since=since)
        for resource in RESOURCES:
            assert ledger.accepted_by(hospital, resource=resource.value) == \
                _reference_accepted_by(ledger, hospital, resource=resource.value)
        for specialty in SPECIALTIES:
            assert ledger.accepted_by(hospital, specialty=specialty.value) == \
                _reference_accepted_by(ledger, hospital,
                                       specialty=specialty.value)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_refusals_by_matches_full_scan(seed):
    ledger, end = _populate(seed)
    for hospital in HOSPITALS + ["NOT_A_HOSPITAL"]:
        assert ledger.refusals_by(hospital) == \
            _reference_refusals_by(ledger, hospital)
        for since in (None, end / 3, end):
            assert ledger.refusals_by(hospital, since=since) == \
                _reference_refusals_by(ledger, hospital, since=since)
        for resource in RESOURCES:
            assert ledger.refusals_by(hospital, resource=resource.value) == \
                _reference_refusals_by(ledger, hospital, resource=resource.value)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_broken_commitments_matches_full_scan(seed):
    ledger, end = _populate(seed)
    for hospital in HOSPITALS:
        for window in (15.0, 60.0, 240.0):
            fast = ledger.broken_commitments(hospital, window_minutes=window)
            slow = _reference_broken(ledger, hospital, window_minutes=window)
            assert fast == slow, (hospital, window)


def test_index_includes_unsealed_pending_entries():
    """A hospital that accepted four minutes ago must be checkable now.

    Tamper-evidence is a property of the sealed chain, but queryability has to
    cover the whole record or the challenge mechanism has a blind spot exactly
    one block wide.
    """
    ledger = HashChainLedger(block_size=1000)   # nothing will seal
    ledger.record_agreement(Agreement(
        request_id="R1", origin="H01", receiver="H02",
        resource=ResourceType.ICU_BED, specialty=Specialty.CARDIAC,
        patient_ref="P1", agreed_at=10.0, decided_by="test"))
    assert len(ledger.chain) == 1, "precondition: nothing sealed yet"
    rows = ledger.accepted_by("H02")
    assert len(rows) == 1
    assert rows[0]["specialty"] == Specialty.CARDIAC.value


def test_index_survives_sealing():
    """Sealing copies the entry list, not the entries, so indices stay valid."""
    ledger = HashChainLedger(block_size=2)
    for i in range(6):
        ledger.record_agreement(Agreement(
            request_id=f"R{i}", origin="H01", receiver="H02",
            resource=ResourceType.ICU_BED, specialty=Specialty.GENERAL,
            patient_ref=f"P{i}", agreed_at=float(i), decided_by="test"))
    assert len(ledger.chain) > 1, "precondition: blocks were sealed"
    assert len(ledger.accepted_by("H02")) == 6
    assert ledger.accepted_by("H02") == _reference_accepted_by(ledger, "H02")


def test_history_still_walks_the_chain():
    """`history()` is untouched: verification and export depend on it."""
    ledger, _ = _populate(7, n=40, block_size=4)
    walked = []
    for block in ledger.chain:
        walked.extend(block.entries)
    walked.extend(ledger._pending)
    assert ledger.history() == walked
    # Indexing must not disturb tamper-evidence.
    assert ledger.verify()["valid"]
