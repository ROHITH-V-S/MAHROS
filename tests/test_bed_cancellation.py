"""A bed given back is not evidence that the hospital had one to give.

When a receiving hospital declines a held bed (broken equipment, no staff, no
answer), the agreement is cancelled on the ledger. It stays on the record, but
the challenge mechanism must stop treating it as "you accepted one a minute
ago", and the fair-share ledger must stop counting it as burden carried.
"""

from __future__ import annotations

from mahros.core.types import (Acuity, Agreement, Bid, ResourceType, Specialty,
                               TransferRequest)
from mahros.fairness.metrics import FairnessLedger
from mahros.ledger.interface import HashChainLedger
from mahros.negotiation.challenge import ChallengeEngine


def _agree(ledger, receiver, at, agreement_id):
    a = Agreement(request_id=f"req-{agreement_id}", origin="H00", receiver=receiver,
                  resource=ResourceType.ICU_BED, specialty=Specialty.GENERAL,
                  patient_ref="pt_x", agreed_at=at, promised_care_start=at + 30)
    a.agreement_id = agreement_id
    ledger.record_agreement(a)
    return a


def _full_refusal(hospital):
    return Bid(bidder=hospital, request_id="req-now", feasible=False,
               refusal_reason="at_self_protection_reserve")


def _req(now):
    return TransferRequest(origin="H00", resource=ResourceType.ICU_BED,
                           specialty=Specialty.GENERAL, acuity=Acuity.CRITICAL,
                           created_at=now, expires_at=now + 150)


def test_given_back_acceptance_is_a_question_not_proof():
    ledger = HashChainLedger(block_size=4)
    _agree(ledger, "H01", at=100.0, agreement_id="agr-1")
    engine = ChallengeEngine(ledger)
    [c] = engine.challenges_for(_full_refusal("H01"), _req(110.0), 110.0)
    assert "It had one to give" in c.claim

    ledger.record_cancellation("agr-1", "H01", "equipment_fault", at=105.0)
    assert ledger.accepted_by("H01") == []
    assert ledger.accepted_between("H01", 0.0, 200.0) == []
    # Still challenged -- accepting and giving back must not wipe the evidence --
    # but the claim says what happened, and does not say it had a bed.
    [c] = engine.challenges_for(_full_refusal("H01"), _req(110.0), 110.0)
    assert "gave it back (equipment fault)" in c.claim
    assert "It had one to give" not in c.claim
    assert "justify" in c.claim


def test_given_back_acceptance_still_proves_capability():
    ledger = HashChainLedger(block_size=4)
    _agree(ledger, "H01", at=100.0, agreement_id="agr-1")
    ledger.record_cancellation("agr-1", "H01", "no_staff", at=101.0)
    assert [e["agreement_id"] for e in
            ledger.accepted_by("H01", include_cancelled=True)] == ["agr-1"]
    no_service = Bid(bidder="H01", request_id="req-now", feasible=False,
                     refusal_reason="no_specialty_capability")
    assert ChallengeEngine(ledger).challenges_for(no_service, _req(110.0), 110.0), \
        "it took a general patient, so it treats general patients"


def test_cancellation_stays_on_the_record_and_survives_a_reload():
    ledger = HashChainLedger(block_size=2)
    _agree(ledger, "H01", at=100.0, agreement_id="agr-1")
    _agree(ledger, "H02", at=101.0, agreement_id="agr-2")
    ledger.record_cancellation("agr-1", "H01", "no_staff", at=102.0)
    ledger.flush()
    assert ledger.verify()["valid"]
    kinds = [e.get("kind", "agreement") for e in ledger.history()]
    assert kinds == ["agreement", "agreement", "cancellation"], "nothing is removed"

    reloaded = HashChainLedger(block_size=2)
    reloaded.load_blocks(ledger.chain)
    assert reloaded.accepted_by("H01") == []
    assert [e["agreement_id"] for e in reloaded.accepted_by("H02")] == ["agr-2"]


def test_cancelled_acceptance_is_not_a_broken_commitment():
    ledger = HashChainLedger(block_size=8)
    ledger.record_refusal("H01", "icu_bed", "req-a", "at_self_protection_reserve", at=100.0)
    _agree(ledger, "H01", at=110.0, agreement_id="agr-1")
    assert ledger.broken_commitments("H01")
    ledger.record_cancellation("agr-1", "H01", "bed_not_ready", at=111.0)
    assert ledger.broken_commitments("H01") == []


def test_withdrawn_acceptance_is_off_the_fair_share_burden():
    f = FairnessLedger(["H01", "H02"])
    f.record_accept("H01", 600.0)
    f.record_accept("H02", 600.0)
    f.record_accept("H01", 300.0)
    f.withdraw_accept("H01", 300.0)
    assert f.records["H01"].accepted == 1
    assert f.recent_load("H01") == 1 and len(f.recent) == 2
    f.withdraw_accept("H01")
    f.withdraw_accept("H01")            # nothing left to withdraw: no error, no negatives
    assert f.records["H01"].accepted == 0 and f.recent_load("H01") == 0


# --------------------------------------------------------------------------- #
# Reliability: hospitals that give beds back rank a little lower
# --------------------------------------------------------------------------- #

def test_reliability_rate_is_smoothed_and_zero_without_give_backs():
    from mahros.fairness.metrics import ReliabilityLedger
    r = ReliabilityLedger(prior=2)
    assert r.rate("H01") == 0.0
    r.record_award("H01")
    assert r.rate("H01") == 0.0
    r.record_give_back("H01")
    assert abs(r.rate("H01") - 1 / 3) < 1e-9, "one of one is 1/3, not 1"
    for _ in range(8):
        r.record_award("H01")
    assert abs(r.rate("H01") - 1 / 11) < 1e-9, "fades as it keeps its word"


def _bid(bidder, ttc, strain=0.5):
    return Bid(bidder=bidder, request_id="req-now", feasible=True,
               travel_minutes=ttc - 10, prep_minutes=10.0, capability_match=1.0,
               post_accept_strain=strain)


def test_without_give_backs_scoring_is_exactly_unchanged():
    from mahros.fairness.metrics import ReliabilityLedger
    from mahros.negotiation.scoring import ScoringWeights, score_bids
    bids = [_bid("H01", 40.0), _bid("H02", 45.0), _bid("H03", 60.0)]
    rel = ReliabilityLedger()
    for h in ("H01", "H02", "H03"):
        rel.record_award(h)
    for acuity in (Acuity.URGENT, Acuity.CRITICAL):
        req = TransferRequest(origin="H00", acuity=acuity, created_at=0.0,
                              expires_at=acuity.safe_window_minutes)
        plain = score_bids(req, bids, 0.0, ScoringWeights())
        with_rel = score_bids(req, bids, 0.0, ScoringWeights(), reliability=rel)
        assert [(s.bid.bidder, s.score) for s in plain] == \
               [(s.bid.bidder, s.score) for s in with_rel]


def test_a_hospital_that_gives_beds_back_loses_a_close_call():
    from mahros.fairness.metrics import ReliabilityLedger
    from mahros.negotiation.scoring import ScoringWeights, score_bids
    # Equally fast; H01 would be a little less stretched after accepting.
    req = TransferRequest(origin="H00", acuity=Acuity.URGENT, created_at=0.0,
                          expires_at=480.0)
    bids = [_bid("H01", 40.0, strain=0.50), _bid("H02", 40.0, strain=0.60)]
    rel = ReliabilityLedger()
    assert score_bids(req, bids, 0.0, ScoringWeights(), reliability=rel)[0].bid.bidder == "H01"
    rel.record_award("H01")
    rel.record_give_back("H01")
    ranked = score_bids(req, bids, 0.0, ScoringWeights(), reliability=rel)
    assert ranked[0].bid.bidder == "H02"
    assert ranked[1].reliability_term < 0 and ranked[0].reliability_term == 0


def test_reliability_never_beats_a_much_faster_hospital():
    from mahros.fairness.metrics import ReliabilityLedger
    from mahros.negotiation.scoring import ScoringWeights, score_bids
    req = TransferRequest(origin="H00", acuity=Acuity.LIFE_THREATENING, created_at=0.0,
                          expires_at=90.0)
    bids = [_bid("H01", 25.0), _bid("H02", 80.0)]
    rel = ReliabilityLedger()
    rel.record_award("H01")
    rel.record_give_back("H01")
    assert score_bids(req, bids, 0.0, ScoringWeights(), reliability=rel)[0].bid.bidder == "H01", \
        "a small penalty: it can decide a close call, not outweigh 55 minutes"
