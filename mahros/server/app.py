"""MAHROS live demo server.

    python -m mahros.server            # then open http://127.0.0.1:8000

Serves a single-page console that drives the real negotiation engine. Protocol
steps are streamed over a WebSocket with a configurable delay between them, so
an audience can watch a call-for-proposals go out, bids and refusals come back
one at a time, the scoring happen, and the agreement get sealed into the ledger.

The pacing delay is presentation only -- it is applied *after* the negotiation
has already run, so nothing about the result depends on it.

With MAHROS_DATABASE_URL set, PostgreSQL is the source of truth: hospital
occupancy and behaviour are read from it before every transfer, every step of
every transfer is written to it, and every change anyone makes is audited by
the database itself. Without it the console runs in memory, as before.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from ..hospital.behaviours import POLICIES
from ..sim.scenario import SCENARIOS
from .live import GIVE_BACK_MINUTES, LiveNetwork

try:  # psycopg is only needed when a database is configured
    from ..db.live_sync import DatabaseSync
    from ..db.store import DataError, Store, actor_label, to_jsonable
except ImportError:  # pragma: no cover - in-memory deployments
    Store = None  # type: ignore[assignment]
    DataError = ValueError  # type: ignore[assignment,misc]

    def actor_label(name):  # type: ignore[no-redef]
        return f"user:{name or 'reviewer'}"

    def to_jsonable(obj):  # type: ignore[no-redef]
        return json.loads(json.dumps(obj, default=str))

STATIC = Path(__file__).resolve().parent / "static"

@asynccontextmanager
async def lifespan(_app):
    # Pass on held beds nobody answered in time (see confirmation_watch).
    watch = asyncio.create_task(confirmation_watch())
    yield
    watch.cancel()


app = FastAPI(title="MAHROS live demo", docs_url="/api/docs", lifespan=lifespan)

#: The live network always runs on the real Houston network in its reference
#: week -- the same week the database's default data comes from.
LIVE_SCENARIO = "houston_baseline"

STORE = Store.from_env() if Store is not None else None


def build_net() -> LiveNetwork:
    """A fresh network: from the database if there is one, else in memory."""
    net = LiveNetwork(scenario=LIVE_SCENARIO, seed=42)
    if STORE is not None:
        net.attach_database(DatabaseSync(STORE))
    else:
        # 55% occupancy: just under the 25th percentile (56%) of US short-term
        # acute hospitals in the HHS data (data/us_hhs_2021w02.json), and the regime where a
        # protective hospital genuinely *has* a bed to give.
        net.seed_load(0.55)
    return net


def _startup_net() -> LiveNetwork:
    """Wait for the database for up to a minute (Docker may still be starting it).

    If it never comes, run in memory and say so on screen, loudly. A console
    that quietly stopped saving would be worse than one that says it cannot.
    """
    global STORE
    if STORE is None:
        return build_net()
    deadline = time.time() + 60
    while True:
        try:
            return build_net()
        except Exception as exc:  # noqa: BLE001 - reported on screen
            if time.time() > deadline:
                STORE = None
                net = build_net()
                net.db_error = f"Database configured but unreachable: {exc}"
                return net
            time.sleep(2)


NET = _startup_net()


def _rebuild() -> None:
    """Reload the whole network from the database (after a restore or a failure)."""
    global NET
    NET = build_net()


# --------------------------------------------------------------------------- #
# request models
# --------------------------------------------------------------------------- #

class TransferBody(BaseModel):
    origin: str
    resource: str = "icu_bed"
    specialty: str = "general"
    acuity: int = 4
    patient_name: str = ""
    patient_note: str = ""
    pace_ms: int = 550
    actor: str | None = None


class ResetBody(BaseModel):
    scenario: str = LIVE_SCENARIO
    seed: int = 42
    load: float = 0.55
    fairness_enabled: bool = True
    llm_enabled: bool = False
    argumentation_enabled: bool = True


class PolicyBody(BaseModel):
    hospital: str
    policy: str = "honest"
    actor: str | None = None


class ToggleBody(BaseModel):
    enabled: bool
    actor: str | None = None


class OccupancyBody(BaseModel):
    hospital: str
    resource: str
    occupied: int
    actor: str | None = None


class HospitalEdit(BaseModel):
    occupied: dict[str, int] | None = None
    policy: str | None = None
    actor: str | None = None


class ActorBody(BaseModel):
    actor: str | None = None


class LoadBody(BaseModel):
    level: float
    actor: str | None = None


def who(request: Request, body_actor: str | None = None) -> str:
    """The person acting: from the request body, or the X-MAHROS-Actor header."""
    return actor_label(body_actor or request.headers.get("x-mahros-actor"))


def err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status)


def need_db() -> JSONResponse | None:
    if STORE is None or not NET.db_mode:
        return err("No database is connected. Start it with `docker compose up` "
                   "(see the README).", 503)
    return None


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #

DASHBOARD = Path(__file__).resolve().parents[2] / "results" / "dashboard.html"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    page = STATIC / "console.html"
    if not page.exists():
        return "<h1>console.html is missing from mahros/server/static/</h1>"
    return page.read_text(encoding="utf-8")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    """The batch-results dashboard, shown in the Results tab.

    Built separately so the results shown are always a completed sweep rather
    than whatever the live network happens to have done since startup -- those
    are different claims and should not be conflated.
    """
    if not DASHBOARD.exists():
        return (
            "<body style='font-family:system-ui;padding:40px;line-height:1.6'>"
            "<h2>No results built yet</h2>"
            "<p>Generate them with:</p>"
            "<pre style='background:#f4f6f8;padding:12px;border-radius:8px'>"
            "python experiments/run_all.py 5\n"
            "python experiments/build_dashboard.py</pre></body>"
        )
    return DASHBOARD.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# REST: the live network
# --------------------------------------------------------------------------- #

@app.get("/healthz")
def healthz():
    """Liveness probe: cheap, and says whether the Results page and database are there."""
    return {
        "status": "ok",
        "hospitals": len(NET.hospitals),
        "dashboard_built": DASHBOARD.exists(),
        "database": NET.db_mode,
    }


@app.get("/api/state")
def get_state():
    return NET.state()


@app.get("/api/scenarios")
def get_scenarios():
    return {
        "scenarios": [
            {
                "name": k,
                "hospitals": v.n_hospitals,
                "capacity_scale": v.capacity_scale,
                "surge_multiplier": v.surge_multiplier,
            }
            for k, v in SCENARIOS.items()
        ]
    }


@app.post("/api/reset")
def reset(body: ResetBody, request: Request):
    """In memory: a new network. With a database: restore the real defaults."""
    global NET
    if NET.db_mode:
        STORE.restore(who(request))
        _rebuild()
        return NET.state()
    if body.scenario not in SCENARIOS:
        return err(f"unknown scenario {body.scenario}")
    NET = LiveNetwork(
        scenario=body.scenario,
        seed=body.seed,
        fairness_enabled=body.fairness_enabled,
        llm_enabled=body.llm_enabled,
        argumentation_enabled=body.argumentation_enabled,
    )
    NET.seed_load(body.load)
    return NET.state()


@app.post("/api/occupancy")
async def set_occupancy(body: OccupancyBody, request: Request):
    try:
        out = NET.set_occupancy(body.hospital, body.resource, body.occupied,
                                actor=who(request, body.actor))
    except (KeyError, ValueError, DataError) as exc:
        return err(str(exc))
    await broadcast_state()
    return out


@app.post("/api/fairness")
def set_fairness(body: ToggleBody, request: Request):
    return NET.set_fairness(body.enabled, actor=who(request, body.actor))


@app.post("/api/policy")
async def set_policy(body: PolicyBody, request: Request):
    """Make one hospital protective, or honest again."""
    try:
        out = NET.set_policy(body.hospital, body.policy, actor=who(request, body.actor))
    except (KeyError, ValueError, DataError) as exc:
        return err(str(exc))
    await broadcast_state()
    return out


@app.post("/api/argumentation")
def set_argumentation(body: ToggleBody, request: Request):
    return NET.set_argumentation(body.enabled, actor=who(request, body.actor))


@app.get("/api/integrity")
def integrity():
    """Who was held to account, and whether anyone was wrongly accused."""
    return NET.integrity()


@app.post("/api/tamper")
def tamper():
    return NET.tamper()


@app.get("/api/ledger")
def ledger():
    return NET.ledger_state()


class TransferNotSaved(RuntimeError):
    """The negotiation ran but the database refused or lost the write."""


def run_transfer(fn):
    """Run one transfer; on any failure with a database, reload from the database.

    The engine changes its own state while negotiating. If the write that
    follows fails, those changes must not survive, or the screen would show a
    transfer the database never stored. Reloading from the database undoes
    them. Bad input (unknown hospital, unknown resource) is reported as is.
    """
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        if not NET.db_mode:
            raise
        _safe_rebuild()
        if isinstance(exc, (KeyError, ValueError)):
            raise
        raise TransferNotSaved(
            f"The transfer was not saved, so it was undone: {exc}") from exc


@app.post("/api/transfer")
def transfer(body: TransferBody, request: Request):
    """Synchronous transfer, for scripting and for the REST docs."""
    try:
        return run_transfer(lambda: NET.request_transfer(
            origin=body.origin,
            resource=body.resource,
            specialty=body.specialty,
            acuity=body.acuity,
            patient_name=body.patient_name,
            patient_note=body.patient_note,
            actor=who(request, body.actor),
        ))
    except (KeyError, ValueError) as exc:
        return err(str(exc))
    except TransferNotSaved as exc:
        return err(str(exc), 500)


def _safe_rebuild() -> None:
    try:
        _rebuild()
    except Exception:  # noqa: BLE001 - the database is down; keep the old network
        pass


# --------------------------------------------------------------------------- #
# REST: the database (Hospital data tab, audit, records)
# --------------------------------------------------------------------------- #

@app.get("/api/db/status")
def db_status():
    if STORE is None or not NET.db_mode:
        return {"connected": False, "error": getattr(NET, "db_error", None)}
    return to_jsonable(STORE.status())


@app.get("/api/hospitals")
def hospitals():
    if (r := need_db()) is not None:
        return r
    return to_jsonable({"hospitals": STORE.hospitals_view(),
                        "policies": sorted(POLICIES),
                        "epoch": STORE.current_epoch()})


@app.patch("/api/hospitals/{hospital_id}")
async def edit_hospital(hospital_id: str, body: HospitalEdit, request: Request):
    """Change occupancy or behaviour. Capacity and specialties are real and locked."""
    if (r := need_db()) is not None:
        return r
    if body.policy is not None and body.policy not in POLICIES:
        return err(f"Unknown behaviour {body.policy!r}.")
    try:
        with NET.lock:
            STORE.update_hospital(who(request, body.actor), hospital_id,
                                  occupied=body.occupied, policy=body.policy)
            NET.sync.pull(NET)
    except DataError as exc:
        return err(str(exc))
    await broadcast_state()
    return to_jsonable({"ok": True, "hospitals": STORE.hospitals_view()})


@app.post("/api/hospitals/{hospital_id}/restore")
async def restore_hospital(hospital_id: str, body: ActorBody, request: Request):
    if (r := need_db()) is not None:
        return r
    try:
        with NET.lock:
            out = STORE.restore(who(request, body.actor), hospital_id)
            NET.sync.pull(NET)
    except DataError as exc:
        return err(str(exc))
    await broadcast_state()
    return to_jsonable({"ok": True, **out})


@app.post("/api/restore")
async def restore_all(body: ActorBody, request: Request):
    """Every hospital back to its real reported data; a new session begins."""
    if (r := need_db()) is not None:
        return r
    with NET.lock:
        out = STORE.restore(who(request, body.actor))
    _rebuild()
    await hub.send_all({"type": "reset"})
    await broadcast_state()
    return to_jsonable({"ok": True, **out})


@app.post("/api/network-load")
async def network_load(body: LoadBody, request: Request):
    if (r := need_db()) is not None:
        return r
    with NET.lock:
        STORE.set_network_load(who(request, body.actor), body.level)
        NET.sync.pull(NET)
    await broadcast_state()
    return {"ok": True, "level": body.level}


class DeclineBody(BaseModel):
    reason: str
    actor: str | None = None


@app.get("/api/inbox/{hospital_id}")
def inbox(hospital_id: str):
    """The receiving hospital's view: beds it is holding, awaiting its confirmation."""
    if (r := need_db()) is not None:
        return r
    box = STORE.inbox(hospital_id)
    for w in box["waiting"]:
        # Seconds until this bed is passed on, when the time limit covers it.
        w["timeout_in"] = None
        if (CONFIRM["timeout_on"] and CONFIRM["since"] is not None
                and w["awarded_at"] >= CONFIRM["since"]
                and STORE.reoffer_chain(w["request_id"])["hops"] < MAX_AUTO_HOPS):
            w["timeout_in"] = max(0.0, CONFIRM["seconds"] - float(w["waiting_seconds"]))
    # This hospital's record: beds awarded and given back this session, and
    # the ranking penalty that follows from it.
    rel = NET.reliability
    box["reliability"] = {"awarded": rel.awarded.get(hospital_id, 0),
                          "given_back": rel.given_back.get(hospital_id, 0),
                          "rate": round(rel.rate(hospital_id), 3),
                          "penalty": round(NET.negotiator.weights.w_reliability
                                           * rel.rate(hospital_id), 3)}
    box["give_back_minutes"] = GIVE_BACK_MINUTES
    return to_jsonable(box)


@app.post("/api/inbox/{request_id}/confirm")
async def confirm_bed(request_id: str, body: ActorBody, request: Request):
    if (r := need_db()) is not None:
        return r
    try:
        out = STORE.confirm_bed(who(request, body.actor), request_id)
    except DataError as exc:
        return err(str(exc))
    await hub.send_all({"type": "inbox"})
    return to_jsonable(out)


#: Bed confirmation time limit. Off by default: when on, a bed held after it
#: was switched on and not answered within `seconds` is passed on to the next
#: hospital automatically. Wall-clock seconds, scaled for a live demo.
CONFIRM = {"timeout_on": False, "seconds": 30, "since": None}
#: A patient is passed on automatically at most this many times in a row;
#: after that nobody is answering, and a person has to pick up the phone.
MAX_AUTO_HOPS = 3


class DeclineFailed(RuntimeError):
    pass


async def decline_and_reoffer(request_id: str, reason: str, actor: str) -> dict:
    """Free the held bed, record why, and offer the patient to the others."""
    chain = STORE.reoffer_chain(request_id)
    held = STORE.decline_bed(actor, request_id, reason)          # DataError if decided
    try:
        result = run_transfer(lambda: NET.reoffer(held, actor=actor, reason=reason,
                                                  also_exclude=chain["declined"]))
    except (KeyError, ValueError, TransferNotSaved) as exc:
        await hub.send_all({"type": "inbox"})
        raise DeclineFailed(
            f"The bed was released, but re-offering the patient failed: {exc}") from exc
    new_id = result["request"]["request_id"]
    receiver = result["agreement"]["receiver"] if result["awarded"] else None
    STORE.record_reoffer(actor, new_id, request_id, receiver)
    context = {"previous_request_id": request_id, "declined_by": held["receiver_id"],
               "declined_by_name": NET.hospitals[held["receiver_id"]].name
               if held["receiver_id"] in NET.hospitals else held["receiver_id"],
               "reason": reason, "runner_up": chain["runner_up"],
               "hop": chain["hops"] + 1,
               "cost_minutes": GIVE_BACK_MINUTES.get(reason, GIVE_BACK_MINUTES["other"])}
    result["reoffer_of"] = context
    # Play the re-offer in every open Live tab, without holding up this reply.
    asyncio.create_task(stream_negotiation(result, 300))
    await hub.send_all({"type": "inbox"})
    return {
        "declined": {"request_id": request_id, "hospital_id": held["receiver_id"],
                     "reason": reason},
        "reoffer": {"request_id": new_id, "awarded": result["awarded"],
                    "receiver": receiver,
                    "receiver_name": (result["agreement"]["receiver_name"]
                                      if result["awarded"] else None),
                    "time_to_care": (result["agreement"].get("time_to_care")
                                     if result["awarded"] else None),
                    "minutes_left": result["request"]["window_min"],
                    "runner_up": chain["runner_up"],
                    "cost_minutes": context["cost_minutes"],
                    "failure_reason": result.get("failure_reason")},
    }


@app.post("/api/inbox/{request_id}/decline")
async def decline_bed(request_id: str, body: DeclineBody, request: Request):
    """Free the held bed, record why, and re-offer the patient to the others."""
    if (r := need_db()) is not None:
        return r
    try:
        out = await decline_and_reoffer(request_id, body.reason, who(request, body.actor))
    except DataError as exc:
        return err(str(exc))
    except DeclineFailed as exc:
        return err(str(exc), 500)
    return to_jsonable(out)


class TimeoutBody(BaseModel):
    on: bool
    actor: str | None = None


@app.get("/api/inbox-settings")
def inbox_settings():
    return {"timeout_on": CONFIRM["timeout_on"], "seconds": CONFIRM["seconds"],
            "max_auto_hops": MAX_AUTO_HOPS}


@app.post("/api/inbox-settings")
async def set_inbox_settings(body: TimeoutBody, request: Request):
    """Switch the confirmation time limit on or off. Applies to beds held from now."""
    CONFIRM["timeout_on"] = body.on
    CONFIRM["since"] = None
    if body.on:
        CONFIRM["since"] = datetime.now(timezone.utc)
        if STORE is not None:       # the database's clock stamps the held beds
            with STORE.tx() as cur:
                cur.execute("SELECT now() AS n")
                CONFIRM["since"] = cur.fetchone()["n"]
    if STORE is not None:
        STORE.audit_action(who(request, body.actor), "CONFIRM_TIMEOUT",
                           {"on": body.on, "seconds": CONFIRM["seconds"]})
    await hub.send_all({"type": "inbox"})
    return inbox_settings()


async def confirmation_watch() -> None:
    """Pass on beds nobody answered in time. Runs for the life of the server."""
    while True:
        await asyncio.sleep(2)
        if not CONFIRM["timeout_on"] or STORE is None or not NET.db_mode:
            continue
        try:
            late = STORE.unanswered(CONFIRM["seconds"], CONFIRM["since"])
            for rid in late:
                if STORE.reoffer_chain(rid)["hops"] >= MAX_AUTO_HOPS:
                    continue      # left waiting: a coordinator has to step in
                try:
                    await decline_and_reoffer(rid, "no_response", "system:confirm-timeout")
                except DataError:
                    pass          # answered at the last moment; nothing to do
        except Exception:  # noqa: BLE001 - keep watching; the next pass retries
            pass


@app.get("/api/hospitals/{hospital_id}/history")
def hospital_history(hospital_id: str, limit: int = 100):
    if (r := need_db()) is not None:
        return r
    return to_jsonable({"history": STORE.hospital_history(hospital_id, limit)})


@app.get("/api/hospitals/{hospital_id}/flags")
def hospital_flags(hospital_id: str):
    if (r := need_db()) is not None:
        return r
    return to_jsonable({"flags": STORE.hospital_flags(hospital_id)})


@app.get("/api/bottlenecks")
def bottlenecks():
    """Regional Monitor: which needs go unmet, which pools are fullest."""
    if (r := need_db()) is not None:
        return r
    return to_jsonable(STORE.bottlenecks())


@app.get("/api/data-integrity")
def data_integrity():
    """What the Data Integrity Agent found in the real HHS reports."""
    if (r := need_db()) is not None:
        return r
    return to_jsonable(STORE.flags_summary())


@app.get("/api/audit")
def audit(limit: int = 100, table: str | None = None, actor: str | None = None,
          row_pk: str | None = None):
    if (r := need_db()) is not None:
        return r
    return to_jsonable({"rows": STORE.audit_rows(limit, table, actor, row_pk)})


@app.get("/api/audit/verify")
def audit_verify():
    """Recompute the audit hash chain inside the database."""
    if (r := need_db()) is not None:
        return r
    return to_jsonable(STORE.audit_verify())


@app.post("/api/audit/checkpoint")
def audit_checkpoint(body: ActorBody, request: Request):
    if (r := need_db()) is not None:
        return r
    return to_jsonable(STORE.audit_checkpoint(who(request, body.actor)))


@app.get("/api/ledger/verify")
def ledger_verify():
    """Recompute the transfer ledger from the rows stored in PostgreSQL."""
    if (r := need_db()) is not None:
        return r
    return to_jsonable(STORE.verify_ledger(NET.sync.epoch))


@app.get("/api/transfers")
def transfers(limit: int = 30):
    if (r := need_db()) is not None:
        return r
    return to_jsonable({"transfers": STORE.transfers(limit)})


@app.get("/api/transfers/{request_id}")
def transfer_trace(request_id: str):
    """One transfer end to end: request, every agent decision, award, ledger, audit."""
    if (r := need_db()) is not None:
        return r
    t = STORE.trace(request_id)
    if t is None:
        return err(f"No transfer {request_id}.", 404)
    return to_jsonable(t)


# --------------------------------------------------------------------------- #
# WebSocket: the live demo
# --------------------------------------------------------------------------- #

class Hub:
    """Broadcasts to every connected console so a room stays in sync."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.lock = asyncio.Lock()

    async def join(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def leave(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def send_all(self, msg: dict) -> None:
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(json.dumps(msg, default=str))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.leave(ws)


hub = Hub()


async def broadcast_state() -> None:
    await hub.send_all({"type": "state", "state": NET.state()})


#: One negotiation on screen at a time: a re-offer started by a timeout waits
#: for the one already playing instead of interleaving with it.
STREAM_LOCK = asyncio.Lock()


async def stream_negotiation(result: dict, pace_ms: int) -> None:
    async with STREAM_LOCK:
        await _stream_negotiation(result, pace_ms)


async def _stream_negotiation(result: dict, pace_ms: int) -> None:
    """Replay a completed negotiation step by step, paced for a human."""
    pace = max(0, min(pace_ms, 3000)) / 1000.0

    await hub.send_all({"type": "busy", "busy": True})
    await hub.send_all({"type": "negotiation_start",
                        "request": result["request"],
                        "privacy": result["privacy"],
                        "reoffer_of": result.get("reoffer_of")})
    await asyncio.sleep(pace)

    for step in result["steps"]:
        # The engine's terminal steps are re-sent below as dedicated
        # `agreement` / `failed` messages carrying the resolved hospital names
        # and the ledger receipt. Forwarding them as raw steps too would make
        # the console render the outcome twice.
        if step["kind"] in ("awarded", "failed"):
            continue
        await hub.send_all({"type": "step", **step})
        # Bids arrive in parallel in the protocol; stagger them slightly here
        # so a viewer can read each one as it lands.
        await asyncio.sleep(pace * (0.45 if step["kind"] == "bid" else 1.0))

    if result["awarded"]:
        await hub.send_all({"type": "agreement", **result["agreement"],
                            "request_id": result["request"]["request_id"],
                            "saved": result.get("db"),
                            "reoffer_of": result.get("reoffer_of")})
    else:
        await hub.send_all({"type": "failed",
                            "request_id": result["request"]["request_id"],
                            "reason": result.get("failure_reason", "unknown")})

    await asyncio.sleep(pace * 0.5)
    await broadcast_state()
    await hub.send_all({"type": "busy", "busy": False})


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await hub.join(ws)
    try:
        await ws.send_text(json.dumps({"type": "state", "state": NET.state()},
                                      default=str))
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            action = msg.get("action")
            actor = actor_label(msg.get("actor"))

            async def fail(text: str) -> None:
                await ws.send_text(json.dumps({"type": "error", "message": text}))
                await ws.send_text(json.dumps({"type": "busy", "busy": False}))

            if action == "transfer":
                try:
                    result = run_transfer(lambda: NET.request_transfer(
                        origin=msg["origin"],
                        resource=msg.get("resource", "icu_bed"),
                        specialty=msg.get("specialty", "general"),
                        acuity=int(msg.get("acuity", 4)),
                        patient_name=msg.get("patient_name", ""),
                        patient_note=msg.get("patient_note", ""),
                        actor=actor,
                    ))
                except (KeyError, ValueError, TransferNotSaved) as exc:
                    await fail(str(exc))
                    continue
                await stream_negotiation(result, int(msg.get("pace_ms", 550)))

            elif action == "auto":
                n = max(1, min(int(msg.get("count", 1)), 25))
                for _ in range(n):
                    try:
                        result = run_transfer(lambda: NET.auto_request(actor=actor))
                    except (KeyError, ValueError, TransferNotSaved) as exc:
                        await fail(str(exc))
                        break
                    await stream_negotiation(result, int(msg.get("pace_ms", 260)))

            elif action == "state":
                await broadcast_state()

            elif action == "tamper":
                await hub.send_all({"type": "tamper", **NET.tamper()})

            elif action == "fairness":
                NET.set_fairness(bool(msg.get("enabled", True)), actor=actor)
                await broadcast_state()

            elif action == "argumentation":
                NET.set_argumentation(bool(msg.get("enabled", True)), actor=actor)
                await broadcast_state()

            elif action == "policy":
                # Make a hospital protective (or honest again). This is the
                # control that turns the demo into an experiment: run the same
                # case before and after, with checking on and off.
                try:
                    res = NET.set_policy(msg["hospital"], msg.get("policy", "honest"),
                                         actor=actor)
                except (KeyError, ValueError) as exc:
                    await fail(str(exc))
                    continue
                await hub.send_all({"type": "policy", **res})
                await broadcast_state()

            elif action == "occupancy":
                try:
                    NET.set_occupancy(msg["hospital"], msg["resource"],
                                      int(msg["occupied"]), actor=actor)
                except (KeyError, ValueError) as exc:
                    await fail(str(exc))
                    continue
                await broadcast_state()

            elif action == "load" and NET.db_mode:
                with NET.lock:
                    STORE.set_network_load(actor, float(msg.get("level", 0.55)))
                    NET.sync.pull(NET)
                await broadcast_state()

            elif action in ("reset", "restore") and NET.db_mode:
                # With a database, "start over" means: back to the real data.
                with NET.lock:
                    STORE.restore(actor)
                _rebuild()
                await hub.send_all({"type": "reset"})
                await broadcast_state()

            elif action == "reset":
                globals()["NET"] = LiveNetwork(
                    scenario=msg.get("scenario", LIVE_SCENARIO),
                    seed=int(msg.get("seed", 42)),
                    fairness_enabled=bool(msg.get("fairness_enabled", True)),
                    argumentation_enabled=bool(msg.get("argumentation_enabled", True)),
                )
                NET.seed_load(float(msg.get("load", 0.62)))
                await hub.send_all({"type": "reset"})
                await broadcast_state()

    except WebSocketDisconnect:
        hub.leave(ws)
    except Exception:
        hub.leave(ws)
