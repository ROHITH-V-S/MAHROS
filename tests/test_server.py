"""Tests for the live demo layer.

The demo is the thing that gets shown to people, so it needs to be as trustworthy
as the batch simulation. These tests assert that it drives the *real* engine and
that the properties the demo visibly claims are actually true.
"""

from __future__ import annotations

import pytest

from mahros.core.types import ResourceType, Specialty
from mahros.server.live import LiveNetwork


@pytest.fixture
def net():
    n = LiveNetwork(scenario="baseline", seed=42)
    n.seed_load(0.6)
    return n


# --------------------------------------------------------------------------- #
# The demo runs the real protocol
# --------------------------------------------------------------------------- #

def test_transfer_emits_full_protocol_trace(net):
    r = net.request_transfer("H00", "icu_bed", "neuro", 5)
    kinds = [s["kind"] for s in r["steps"]]
    assert kinds[0] == "cfp"
    assert "bid" in kinds
    assert "scored" in kinds
    assert kinds[-1] in ("awarded", "failed")


def test_every_contacted_peer_produces_a_bid_event(net):
    r = net.request_transfer("H00", "icu_bed", "general", 4)
    cfp = next(s for s in r["steps"] if s["kind"] == "cfp")
    bids = [s for s in r["steps"] if s["kind"] == "bid"]
    assert len(bids) == len(cfp["peers"])


def test_refusals_carry_a_reason(net):
    """A bid the audience sees refused must say why — no silent 'no'."""
    r = net.request_transfer("H00", "cath_lab", "cardiac", 5)
    refusals = [s for s in r["steps"] if s["kind"] == "bid" and not s["feasible"]]
    assert refusals, "expected some refusals for a scarce specialty resource"
    assert all(s["reason"] for s in refusals)


def test_award_carries_rationale_and_ledger_hash(net):
    for _ in range(6):
        r = net.request_transfer("H00", "icu_bed", "general", 3)
        if r["awarded"]:
            assert r["agreement"]["rationale"]
            assert len(r["agreement"]["terms_hash"]) == 64
            return
    pytest.fail("no transfer succeeded in six attempts on a 60%-loaded network")


# --------------------------------------------------------------------------- #
# The privacy claim the demo makes on screen
# --------------------------------------------------------------------------- #

def test_note_is_scrubbed_and_identifiers_never_cross(net):
    note = "Mr. Rahul Sharma, MRN H03-000891, ph +91 9812345678, email r@x.com"
    r = net.request_transfer("H00", "icu_bed", "general", 4,
                             patient_name="Priya Nair", patient_note=note)

    scrubbed = r["privacy"]["scrubbed_note"]
    for leak in ("Rahul", "H03-000891", "9812345678", "r@x.com"):
        assert leak not in scrubbed

    crossing = str(r["privacy"]["crosses_boundary"])
    for leak in ("Rahul", "Priya", "H03-000891", "9812345678"):
        assert leak not in crossing

    assert r["request"]["patient_ref"].startswith("pt_")
    assert net.privacy.report()["clean"]


def test_public_view_has_no_identifying_keys(net):
    r = net.request_transfer("H00", "icu_bed", "general", 4, patient_name="Priya Nair")
    for key in ("name", "mrn", "phone", "age", "group"):
        assert key not in r["privacy"]["crosses_boundary"]


# --------------------------------------------------------------------------- #
# Length of stay must match what was asked for
# --------------------------------------------------------------------------- #

def test_los_matches_requested_resource(net):
    """An ICU request must not be handed a three-hour cath-lab stay."""
    icu = [net.request_transfer("H00", "icu_bed", "general", 3)["request"]["expected_los_hours"]
           for _ in range(8)]
    cath = [net.request_transfer("H00", "cath_lab", "cardiac", 4)["request"]["expected_los_hours"]
            for _ in range(8)]
    assert min(icu) > max(cath), "ICU stays should dominate cath-lab stays"


# --------------------------------------------------------------------------- #
# Demo controls behave
# --------------------------------------------------------------------------- #

def test_seed_load_sets_occupancy(net):
    net.seed_load(0.9)
    strains = [h.resources.overall_strain() for h in net.hospitals.values()]
    assert sum(strains) / len(strains) > 0.7


def test_set_occupancy_changes_bidding(net):
    """Filling a hospital must make it stop bidding — the 'what if' control."""
    target = "H11"
    net.set_occupancy(target, "icu_bed", 0)
    r1 = net.request_transfer("H00", "icu_bed", "neuro", 4)
    bid1 = next((s for s in r1["steps"] if s["kind"] == "bid" and s["bidder"] == target), None)

    pool = net.hospitals[target].resources.pool(ResourceType.ICU_BED)
    net.set_occupancy(target, "icu_bed", pool.capacity)
    r2 = net.request_transfer("H00", "icu_bed", "neuro", 4)
    bid2 = next((s for s in r2["steps"] if s["kind"] == "bid" and s["bidder"] == target), None)

    assert bid1 and bid1["feasible"]
    assert bid2 and not bid2["feasible"]


def test_tamper_breaks_the_chain(net):
    for _ in range(12):
        net.request_transfer("H00", "icu_bed", "general", 3)
    assert net.ledger_state()["valid"]
    res = net.tamper()
    assert res["tampered"]
    assert not res["ledger"]["valid"]


def test_tamper_without_agreements_is_reported_not_crashed(net):
    res = net.tamper()
    assert res["tampered"] is False
    assert "message" in res


def test_fairness_toggle_reaches_the_negotiator(net):
    net.set_fairness(False)
    assert net.negotiator.weights.fairness_enabled is False
    net.set_fairness(True)
    assert net.negotiator.weights.fairness_enabled is True


def test_state_is_json_serialisable(net):
    import json
    net.request_transfer("H00", "icu_bed", "general", 4)
    json.dumps(net.state(), default=str)


def test_clock_advances_with_each_negotiation(net):
    t0 = net.now
    net.request_transfer("H00", "icu_bed", "general", 4)
    assert net.now > t0


def test_unknown_hospital_raises(net):
    with pytest.raises(KeyError):
        net.request_transfer("H99", "icu_bed", "general", 4)


# --------------------------------------------------------------------------- #
# The decentralisation claim shown in the console header
# --------------------------------------------------------------------------- #

def test_no_node_brokers_a_majority(net):
    for _ in range(20):
        net.auto_request()
    d = net.state()["decentralisation"]
    assert d["is_decentralised"]
    assert d["decision_concentration"] < 0.5


# --------------------------------------------------------------------------- #
# HTTP + WebSocket surface
# --------------------------------------------------------------------------- #

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from mahros.server.app import app
    return TestClient(app)


def test_index_and_state_endpoints(client):
    assert client.get("/").status_code == 200
    body = client.get("/api/state").json()
    assert len(body["hospitals"]) > 0
    assert body["ledger"]["valid"]


def test_console_has_both_tabs(client):
    html = client.get("/").text
    assert 'data-view="live"' in html
    assert 'data-view="results"' in html
    assert 'id="autorun"' in html


def test_dashboard_route_serves_or_explains(client):
    """The Results tab must never render a blank frame."""
    r = client.get("/dashboard")
    assert r.status_code == 200
    body = r.text
    # Either the built dashboard, or actionable instructions for building it.
    assert ("MAHROS" in body) or ("run_all.py" in body)
    assert len(body) > 200


def test_transfer_endpoint(client):
    r = client.post("/api/transfer", json={
        "origin": "H00", "resource": "icu_bed", "specialty": "general", "acuity": 4,
    })
    assert r.status_code == 200
    assert "steps" in r.json()


def test_transfer_endpoint_rejects_bad_input(client):
    r = client.post("/api/transfer", json={"origin": "NOPE", "resource": "icu_bed"})
    assert r.status_code == 400


def test_websocket_streams_a_negotiation(client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "state"
        ws.send_json({"action": "transfer", "origin": "H00", "resource": "icu_bed",
                      "specialty": "general", "acuity": 4, "pace_ms": 0})
        types = []
        for _ in range(60):
            m = ws.receive_json()
            types.append(m["type"])
            if m["type"] == "state":
                break
        assert types[0] == "negotiation_start"
        assert "step" in types
        assert ("agreement" in types) or ("failed" in types)


def test_websocket_does_not_double_report_the_outcome(client):
    """The engine's terminal step must not also arrive as a raw step."""
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"action": "transfer", "origin": "H00", "resource": "icu_bed",
                      "specialty": "general", "acuity": 4, "pace_ms": 0})
        step_kinds = []
        for _ in range(60):
            m = ws.receive_json()
            if m["type"] == "step":
                step_kinds.append(m["kind"])
            if m["type"] == "state":
                break
        assert "awarded" not in step_kinds
        assert "failed" not in step_kinds
