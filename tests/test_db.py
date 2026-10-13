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
