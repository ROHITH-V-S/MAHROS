"""PostgreSQL: locks on real data, the audit chain, and transfers written through.

Runs against a throwaway database `mahros_test` on the server named by
MAHROS_TEST_ADMIN_URL (the owner login), e.g. with `docker compose up -d db`:

    MAHROS_TEST_ADMIN_URL=postgresql://mahros_admin:mahros-admin-local@localhost:5440/mahros

Skipped when that variable is not set, so the rest of the suite needs no
database.
"""

from __future__ import annotations

import os
import re

import pytest

psycopg = pytest.importorskip("psycopg")

ADMIN = os.environ.get("MAHROS_TEST_ADMIN_URL", "")
pytestmark = pytest.mark.skipif(not ADMIN, reason="MAHROS_TEST_ADMIN_URL not set")

TEST_DB = "mahros_test"
#: mahros_app is a server-wide role; use the same password the demo uses so a
#: test run never locks the running dashboard out.
APP_PASSWORD = os.environ.get("MAHROS_TEST_APP_PASSWORD", "mahros-app-local")


def _url_for(url: str, db: str) -> str:
    return re.sub(r"/[^/?]+(\?|$)", f"/{db}\\1", url, count=1) if re.search(
        r"://[^/]+/[^/?]+", url) else url.rstrip("/") + f"/{db}"


@pytest.fixture(scope="module")
def urls():
    from mahros.db.admin import init_database
    from mahros.db.store import plain_url

    server = plain_url(ADMIN)
    with psycopg.connect(server, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        c.execute(f"CREATE DATABASE {TEST_DB}")
    admin = _url_for(server, TEST_DB)
    init_database(admin, APP_PASSWORD)
    app = re.sub(r"://[^@]+@", f"://mahros_app:{APP_PASSWORD}@", admin)
    yield {"admin": admin, "app": app}
    with psycopg.connect(server, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")


@pytest.fixture
def store(urls):
    from mahros.db.store import Store
    s = Store(urls["app"])
    s.restore("user:test-setup")
    yield s
    s.close()


@pytest.fixture
def admin(urls):
    with psycopg.connect(urls["admin"], autocommit=True) as c:
        yield c


def _net(store):
    from mahros.db.live_sync import DatabaseSync
    from mahros.server.live import LiveNetwork
    net = LiveNetwork(scenario="houston_baseline", seed=42)
    net.attach_database(DatabaseSync(store))
    return net


def _pool(store, hospital_id, kind):
    with store.tx() as cur:
        cur.execute("SELECT * FROM resources WHERE hospital_id = %s AND kind = %s",
                    (hospital_id, kind))
        return cur.fetchone()


# --------------------------------------------------------------------------- #
# Real data is loaded, once, and locked
# --------------------------------------------------------------------------- #

def test_real_reference_data_is_loaded(store):
    counts = store.status()["counts"]
    assert counts["hospitals"] == 27
    assert counts["hhs_weekly_reports"] == 27 * 216
    assert counts["resources"] > 100
    assert counts["report_flags"] > 0


def test_init_is_safe_to_repeat(urls, store):
    from mahros.db.admin import init_database
    out = init_database(urls["admin"], APP_PASSWORD)
    assert out["loaded"] == {}
    assert store.status()["counts"]["hospitals"] == 27


def test_default_occupancy_is_the_reported_week(store):
    """Memorial Hermann TMC reported 119 of 168 staffed adult ICU beds in use."""
    row = _pool(store, "450068", "icu_bed")
    assert row["capacity"] == 168
    assert row["default_occupied"] == 119
    assert row["default_provenance"] == "observed"


def test_app_cannot_change_capacity(store):
    from mahros.db.store import DataError
    with pytest.raises(DataError, match="locked"):
        with store.tx("user:test") as cur:
            cur.execute("UPDATE resources SET capacity = 1 WHERE hospital_id = '450068'")


@pytest.mark.parametrize("sql", [
    "UPDATE resources SET capacity = 1 WHERE resource_id = 1",
    "UPDATE hhs_weekly_reports SET inpatient_used = 1 WHERE hospital_id = '450068'",
    "DELETE FROM hospital_capabilities WHERE hospital_id = '450068'",
    "UPDATE hospitals SET name = 'Forged' WHERE hospital_id = '450068'",
])
def test_even_the_owner_cannot_change_real_data(admin, sql):
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="locked"):
        admin.execute(sql)


@pytest.mark.parametrize("sql", [
    "DELETE FROM audit_log",
    "UPDATE audit_log SET actor = 'nobody'",
    "DELETE FROM agent_decisions",
    "UPDATE ledger_entries SET payload = '{}'",
    "UPDATE bed_confirmations SET outcome = 'confirmed'",
    "DELETE FROM bed_confirmations",
])
def test_records_are_append_only_for_the_app(store, sql):
    from mahros.db.store import DataError
    with pytest.raises(DataError):
        with store.tx("user:test") as cur:
            cur.execute(sql)


# --------------------------------------------------------------------------- #
# Edits are validated and audited
# --------------------------------------------------------------------------- #

def test_edit_is_audited_with_before_after_and_who(store):
    store.update_hospital("user:Asha", "450358", occupied={"icu_bed": 60})
    row = store.audit_rows(1)[0]
    assert row["actor"] == "user:Asha"
    assert row["table_name"] == "resources" and row["action"] == "UPDATE"
    assert row["before"]["occupied"] != 60 and row["after"]["occupied"] == 60
    assert row["before"]["capacity"] == row["after"]["capacity"]


def test_policy_change_is_audited(store):
    store.update_hospital("user:Asha", "450358", policy="strategic")
    hist = store.hospital_history("450358")
    assert hist[0]["table_name"] == "hospital_settings"
    assert hist[0]["after"]["policy"] == "strategic"


def test_occupancy_beyond_capacity_is_refused(store):
    from mahros.db.store import DataError
    with pytest.raises(DataError, match="0 to 168"):
        store.update_hospital("user:test", "450068", occupied={"icu_bed": 500})


def test_constraint_holds_even_if_validation_is_bypassed(store):
    from mahros.db.store import DataError
    with pytest.raises(DataError, match="real capacity"):
        with store.tx("user:test") as cur:
            cur.execute("UPDATE resources SET occupied = capacity + 1 "
                        "WHERE resource_id = 1")


# --------------------------------------------------------------------------- #
# Tamper detection
# --------------------------------------------------------------------------- #

def test_audit_chain_verifies_and_catches_an_edit(store, admin):
    store.update_hospital("user:test", "450068", occupied={"ward_bed": 700})
    assert store.audit_verify()["valid"]
    target = store.audit_rows(1)[0]["audit_id"]
    admin.execute("UPDATE audit_log SET actor = 'user:someone-else' "
                  "WHERE audit_id = %s", (target,))
    v = store.audit_verify()
    assert not v["valid"] and v["first_bad_id"] == target
    admin.execute("UPDATE audit_log SET actor = 'user:test' WHERE audit_id = %s",
                  (target,))
    assert store.audit_verify()["valid"]


def test_a_deleted_audit_row_breaks_the_chain(store, admin):
    store.update_hospital("user:test", "450068", occupied={"ward_bed": 701})
    store.update_hospital("user:test", "450068", occupied={"ward_bed": 702})
    rows = store.audit_rows(2)
    victim = rows[1]
    admin.execute("DELETE FROM audit_log WHERE audit_id = %s", (victim["audit_id"],))
    v = store.audit_verify()
    assert not v["valid"] and v["first_bad_id"] == rows[0]["audit_id"]
    cols = ("audit_id", "ts", "actor", "action", "table_name", "row_pk", "before",
            "after", "prev_hash", "row_hash")
    from psycopg.types.json import Jsonb
    admin.execute(
        f"INSERT INTO audit_log ({', '.join(cols)}) VALUES ({', '.join(['%s'] * 10)})",
        [Jsonb(victim[c]) if c in ("before", "after") and victim[c] is not None
         else victim[c] for c in cols])
    assert store.audit_verify()["valid"]


def test_checkpoint_catches_a_cut_off_tail(store, admin):
    store.update_hospital("user:test", "450068", occupied={"ward_bed": 703})
    cp = store.audit_checkpoint("user:test")
    assert store.audit_verify()["valid"]
    admin.execute("UPDATE audit_checkpoints SET row_hash = repeat('f', 64) "
                  "WHERE checkpoint_id = %s", (cp["checkpoint_id"],))
    v = store.audit_verify()
    assert not v["valid"] and "checkpoint" in v["problem"]
    admin.execute("DELETE FROM audit_checkpoints")


# --------------------------------------------------------------------------- #
# Transfers are written through
# --------------------------------------------------------------------------- #

def test_transfer_is_written_in_full(store):
    net = _net(store)
    result = net.request_transfer(origin="450097", resource="icu_bed",
                                  specialty="general", acuity=4, actor="user:test")
    assert result["awarded"]
    receiver = result["agreement"]["receiver"]
    trace = store.trace(result["request"]["request_id"])
    assert trace["allocation"]["receiver_id"] == receiver
    assert trace["admission"]["hospital_id"] == receiver
    assert trace["ledger_block"] is not None
    actions = [d["action"] for d in trace["decisions"]]
    assert actions[0] == "cfp" and "award" in actions
    assert len(trace["audit"]) >= len(trace["decisions"])
    assert all("opportunity_cost" not in d["payload"] for d in trace["decisions"]), \
        "a bidder's private valuation must never reach the shared record"
    assert store.verify_ledger(net.sync.epoch)["valid"]


def test_awarded_bed_is_taken_in_the_database(store):
    net = _net(store)
    before = {(p["hospital_id"], p["kind"]): p["occupied"]
              for p in store.live_state()["pools"]}
    r = net.request_transfer(origin="450097", resource="icu_bed",
                             specialty="general", acuity=4, actor="user:test")
    rcv = r["agreement"]["receiver"]
    assert _pool(store, rcv, "icu_bed")["occupied"] == before[(rcv, "icu_bed")] + 1


def test_held_bed_waits_in_the_receivers_inbox_until_confirmed(store):
    net = _net(store)
    r = net.request_transfer(origin="450097", resource="icu_bed",
                             specialty="general", acuity=4, actor="user:test")
    rid, rcv = r["request"]["request_id"], r["agreement"]["receiver"]
    box = store.inbox(rcv)
    assert [w["request_id"] for w in box["waiting"]] == [rid]
    assert box["waiting_by_hospital"] == {rcv: 1}
    store.confirm_bed("user:bed-manager", rid)
    box = store.inbox(rcv)
    assert box["waiting"] == [] and box["decided"][0]["outcome"] == "confirmed"


def test_declined_bed_is_freed_and_the_patient_reoffered_elsewhere(store):
    from mahros.db.store import DataError
    net = _net(store)
    r = net.request_transfer(origin="450097", resource="icu_bed",
                             specialty="general", acuity=4, actor="user:test")
    rid, rcv = r["request"]["request_id"], r["agreement"]["receiver"]
    taken = _pool(store, rcv, "icu_bed")["occupied"]

    held = store.decline_bed("user:bed-manager", rid, "equipment_fault")
    assert _pool(store, rcv, "icu_bed")["occupied"] == taken - 1
    assert store.inbox(rcv)["waiting"] == []

    again = net.reoffer(held, actor="user:test")
    assert again["request"]["patient_ref"] == r["request"]["patient_ref"]
    asked = next(s for s in again["steps"] if s["kind"] == "cfp")["peers"]
    assert rcv not in asked, "the hospital that declined must not be asked again"
    assert again["request"]["window_min"] < r["request"]["window_min"], \
        "the clinical clock keeps running from the original request"
    store.record_reoffer("user:test", again["request"]["request_id"], rid,
                         again["agreement"]["receiver"] if again["awarded"] else None)
    trace = store.trace(rid)
    assert [c["outcome"] for c in trace["confirmations"]] == ["declined", "reoffered"]
    with pytest.raises(DataError):
        store.confirm_bed("user:bed-manager", rid)   # decided once, never twice


def test_time_limit_only_covers_beds_held_after_it_was_switched_on(store):
    net = _net(store)
    before = net.request_transfer(origin="450097", resource="icu_bed",
                                  specialty="general", acuity=4, actor="user:test")
    with store.tx() as cur:
        cur.execute("SELECT now() AS n")
        switched_on = cur.fetchone()["n"]
    after = net.request_transfer(origin="450097", resource="icu_bed",
                                 specialty="general", acuity=4, actor="user:test")
    late = store.unanswered(0, switched_on)
    assert after["request"]["request_id"] in late
    assert before["request"]["request_id"] not in late, "never retroactive"
    assert store.unanswered(3600, switched_on) == [], "not late yet"
    rid = after["request"]["request_id"]
    held = store.decline_bed("system:confirm-timeout", rid, "no_response")
    again = net.reoffer(held, actor="system:confirm-timeout")
    store.record_reoffer("system:confirm-timeout", again["request"]["request_id"], rid,
                         again["agreement"]["receiver"] if again["awarded"] else None)
    chain = store.reoffer_chain(again["request"]["request_id"])
    assert chain["hops"] == 1
    assert store.reoffer_chain(rid)["runner_up"] is not None


def _decline(store, net, rid, reason):
    """What the server does on a decline: free, cancel, re-offer, link."""
    chain = store.reoffer_chain(rid)
    held = store.decline_bed("user:bed-manager", rid, reason)
    again = net.reoffer(held, actor="user:test", reason=reason,
                        also_exclude=chain["declined"])
    store.record_reoffer("user:test", again["request"]["request_id"], rid,
                         again["agreement"]["receiver"] if again["awarded"] else None)
    return again


def test_no_hospital_that_declined_is_asked_again_down_the_chain(store):
    net = _net(store)
    r = net.request_transfer(origin="450097", resource="icu_bed",
                             specialty="general", acuity=4, actor="user:test")
    first = r["agreement"]["receiver"]
    r2 = _decline(store, net, r["request"]["request_id"], "equipment_fault")
    second = r2["agreement"]["receiver"]
    r3 = _decline(store, net, r2["request"]["request_id"], "no_staff")
    asked = next(s for s in r3["steps"] if s["kind"] == "cfp")["peers"]
    assert first not in asked and second not in asked
    assert store.reoffer_chain(r3["request"]["request_id"])["hops"] == 2
    assert r3["request"]["window_min"] <= r2["request"]["window_min"] \
        <= r["request"]["window_min"]


def test_declined_acceptance_stops_being_evidence_and_burden(store):
    net = _net(store)
    r = net.request_transfer(origin="450097", resource="icu_bed",
                             specialty="general", acuity=4, actor="user:test")
    rcv, agr = r["agreement"]["receiver"], r["agreement"]["agreement_id"]
    assert [e["agreement_id"] for e in net.ledger.accepted_by(rcv)] == [agr]
    load = net.fairness.recent_load(rcv)
    _decline(store, net, r["request"]["request_id"], "bed_not_ready")
    assert net.ledger.accepted_by(rcv) == []
    assert net.fairness.recent_load(rcv) == load - 1
    # ... and the same after a restart, rebuilt from the database
    fresh = _net(store)
    assert any(e.get("agreement_id") == agr and "receiver" in e
               for e in fresh.ledger.history()), "the agreement is still on the record"
    assert fresh.ledger.accepted_by(rcv) == []
    assert fresh.fairness.recent_load(rcv) == load - 1


def test_accept_then_give_back_does_not_wipe_the_evidence(store):
    """The loophole: take a patient, give it back, then say 'full' with a clean record."""
    net = _net(store)
    store.update_hospital("user:test", "450358", occupied={"icu_bed": 100},
                          policy="strategic")
    r = net.request_transfer(origin="450076", resource="icu_bed", specialty="general",
                             acuity=5, actor="user:test")
    assert r["agreement"]["receiver"] == "450358"
    _decline(store, net, r["request"]["request_id"], "equipment_fault")
    lie = net.request_transfer(origin="450076", resource="icu_bed", specialty="general",
                               acuity=4, actor="user:test")
    [c] = [s for s in lie["steps"] if s["kind"] == "challenge" and s["target"] == "450358"]
    assert "gave it back" in c["claim"]
    assert any(s["kind"] == "overruled" and s["bidder"] == "450358" for s in lie["steps"]), \
        "a protective hospital with free beds cannot defend the refusal"


def test_an_honest_hospital_that_gave_a_bed_back_defends_itself(store):
    net = _net(store)
    store.update_hospital("user:test", "450358", occupied={"icu_bed": 100})
    r = net.request_transfer(origin="450076", resource="icu_bed", specialty="general",
                             acuity=5, actor="user:test")
    _decline(store, net, r["request"]["request_id"], "equipment_fault")
    store.update_hospital("user:test", "450358", occupied={"icu_bed": 158})   # really full
    again = net.request_transfer(origin="450076", resource="icu_bed", specialty="general",
                                 acuity=4, actor="user:test")
    assert any(s["kind"] == "challenge" and s["target"] == "450358" for s in again["steps"])
    [d] = [s for s in again["steps"] if s["kind"] == "defence" and s["defender"] == "450358"]
    assert d["upheld"], "its own beds back the refusal"
    assert not any(s["kind"] == "overruled" and s["bidder"] == "450358"
                   for s in again["steps"])


def test_a_give_back_costs_the_patient_time(store):
    from mahros.server.live import GIVE_BACK_MINUTES
    net = _net(store)
    r = net.request_transfer(origin="450097", resource="icu_bed",
                             specialty="general", acuity=4, actor="user:test")
    a = _decline(store, net, r["request"]["request_id"], "no_staff")
    lost = r["request"]["window_min"] - a["request"]["window_min"]
    assert lost >= GIVE_BACK_MINUTES["no_staff"], f"only {lost} min lost"
    b = _decline(store, net, a["request"]["request_id"], "no_response")
    lost = a["request"]["window_min"] - b["request"]["window_min"]
    assert lost >= GIVE_BACK_MINUTES["no_response"], f"only {lost} min lost"


def test_reliability_follows_give_backs_and_survives_a_restart(store):
    net = _net(store)
    r = net.request_transfer(origin="450097", resource="icu_bed",
                             specialty="general", acuity=4, actor="user:test")
    rcv = r["agreement"]["receiver"]
    assert net.reliability.rate(rcv) == 0.0
    again = _decline(store, net, r["request"]["request_id"], "bed_not_ready")
    rate = net.reliability.rate(rcv)
    assert rate > 0
    fresh = _net(store)
    assert fresh.reliability.rate(rcv) == rate
    other = again["agreement"]["receiver"]
    assert fresh.reliability.rate(other) == 0.0


def test_a_bed_cannot_be_decided_twice_even_bypassing_the_app(store):
    from mahros.db.store import DataError
    net = _net(store)
    r = net.request_transfer(origin="450097", resource="icu_bed",
                             specialty="general", acuity=4, actor="user:test")
    rid, rcv = r["request"]["request_id"], r["agreement"]["receiver"]
    store.confirm_bed("user:bed-manager", rid)
    with pytest.raises(DataError):
        with store.tx("user:test") as cur:
            cur.execute("INSERT INTO bed_confirmations (request_id, hospital_id, outcome, "
                        "reason) VALUES (%s, %s, 'declined', 'no_staff')", (rid, rcv))


def test_engine_uses_the_database_values(store):
    """Fill every ICU but one in the database: the patient must go to that one."""
    with store.tx() as cur:
        cur.execute("SELECT hospital_id, capacity FROM resources WHERE kind = 'icu_bed'")
        icus = cur.fetchall()
    keep = "450035"
    for row in icus:
        store.update_hospital("user:test", row["hospital_id"], occupied={
            "icu_bed": 0 if row["hospital_id"] == keep else row["capacity"]})
    net = _net(store)
    r = net.request_transfer(origin="450097", resource="icu_bed", specialty="general",
                             acuity=3, actor="user:test")
    assert r["awarded"] and r["agreement"]["receiver"] == keep


def test_occupancy_cannot_drop_below_patients_transferred_in(store):
    from mahros.db.store import DataError
    net = _net(store)
    r = net.request_transfer(origin="450097", resource="icu_bed", specialty="general",
                             acuity=4, actor="user:test")
    rcv = r["agreement"]["receiver"]
    with pytest.raises(DataError, match="transferred in"):
        store.update_hospital("user:test", rcv, occupied={"icu_bed": 0})


def test_ledger_tamper_in_the_database_is_caught(store, admin):
    net = _net(store)
    net.request_transfer(origin="450097", resource="icu_bed", specialty="general",
                         acuity=4, actor="user:test")
    epoch = net.sync.epoch
    admin.execute("""
        UPDATE ledger_entries SET payload = jsonb_set(payload, '{receiver}', '"H99"')
        WHERE epoch = %s AND payload ? 'receiver'""", (epoch,))
    v = store.verify_ledger(epoch)
    assert not v["valid"] and "Merkle" in v["errors"][0]


def test_restart_resumes_the_session(store):
    net = _net(store)
    for _ in range(3):
        net.auto_request(actor="user:test")
    totals = net.state()["totals"]
    head = net.ledger.chain[-1].hash
    again = _net(store)
    assert again.state()["totals"]["negotiations"] == totals["negotiations"]
    assert again.ledger.chain[-1].hash == head
    assert again.ledger.verify()["valid"]


def test_restore_puts_real_data_back_and_keeps_history(store):
    net = _net(store)
    net.request_transfer(origin="450097", resource="icu_bed", specialty="general",
                         acuity=4, actor="user:test")
    store.update_hospital("user:test", "450358", occupied={"icu_bed": 10},
                          policy="strategic")
    n_transfers = store.status()["counts"]["transfer_requests"]
    epoch = store.current_epoch()["epoch"]

    out = store.restore("user:test")

    assert out["new_epoch"] == epoch + 1
    row = _pool(store, "450358", "icu_bed")
    assert row["occupied"] == row["default_occupied"]
    assert store.live_state()["policies"]["450358"] == "honest"
    with store.tx() as cur:
        cur.execute("SELECT count(*) AS n FROM admissions WHERE discharged_at IS NULL")
        assert cur.fetchone()["n"] == 0
    assert store.status()["counts"]["transfer_requests"] == n_transfers, \
        "restore must never delete history"
    assert store.audit_verify()["valid"]


def test_restore_one_hospital_only(store):
    store.update_hospital("user:test", "450358", occupied={"icu_bed": 10})
    store.update_hospital("user:test", "450068", occupied={"icu_bed": 10})
    store.restore("user:test", "450358")
    assert _pool(store, "450358", "icu_bed")["occupied"] == \
        _pool(store, "450358", "icu_bed")["default_occupied"]
    assert _pool(store, "450068", "icu_bed")["occupied"] == 10


def test_network_load_never_evicts_transferred_patients(store):
    net = _net(store)
    r = net.request_transfer(origin="450097", resource="icu_bed", specialty="general",
                             acuity=4, actor="user:test")
    rcv = r["agreement"]["receiver"]
    store.set_network_load("user:test", 0.0)
    assert _pool(store, rcv, "icu_bed")["occupied"] == 1


def test_bottlenecks_report_unmet_needs_and_fullest_pools(store):
    """Fill every ICU, ask for an ICU bed: the monitor names ICU as the gap."""
    with store.tx() as cur:
        cur.execute("SELECT hospital_id, capacity FROM resources WHERE kind = 'icu_bed'")
        icus = cur.fetchall()
    for row in icus:
        store.update_hospital("user:test", row["hospital_id"],
                              occupied={"icu_bed": row["capacity"]})
    net = _net(store)
    r = net.request_transfer(origin="450097", resource="icu_bed", specialty="general",
                             acuity=3, actor="user:test")
    assert not r["awarded"]
    b = store.bottlenecks()
    assert b["failed"] >= 1 and "icu bed" in b["headline"]
    assert b["pools"][0]["kind"] == "icu_bed" and b["pools"][0]["occupancy"] == 1.0


def test_loader_refreshes_stale_defaults_and_audits_it(urls, store, admin):
    """Only the loader may rewrite locked defaults; init re-derives them."""
    from mahros.db.admin import init_database
    admin.execute("BEGIN")
    admin.execute("SELECT set_config('mahros.loading_reference', 'on', true)")
    admin.execute("UPDATE resources SET default_occupied = 0 "
                  "WHERE hospital_id = '450068' AND kind = 'icu_bed'")
    admin.execute("COMMIT")
    out = init_database(urls["admin"], APP_PASSWORD)
    assert out["loaded"] == {"defaults_refreshed": 1}
    assert _pool(store, "450068", "icu_bed")["default_occupied"] == 119
    row = store.audit_rows(5, table="resources")[0]
    assert row["actor"] == "system:data-loader"
    assert row["after"]["default_occupied"] == 119
