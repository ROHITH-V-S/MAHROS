"""MAHROS live demo server.

    python -m mahros.server            # then open http://127.0.0.1:8000

Serves a single-page console that drives the real negotiation engine. Protocol
steps are streamed over a WebSocket with a configurable delay between them, so
an audience can watch a call-for-proposals go out, bids and refusals come back
one at a time, the scoring happen, and the agreement get sealed into the ledger.

The pacing delay is presentation only -- it is applied *after* the negotiation
has already run, so nothing about the result depends on it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from ..sim.scenario import SCENARIOS
from .live import LiveNetwork

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="MAHROS live demo", docs_url="/api/docs")

# One shared network per server process. This is a demo console, not a
# multi-tenant service -- everyone connected watches the same hospitals, which
# is exactly what you want when projecting it in a room.
NET = LiveNetwork(scenario="baseline", seed=42)
NET.seed_load(0.62)


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


class ResetBody(BaseModel):
    scenario: str = "baseline"
    seed: int = 42
    load: float = 0.62
    fairness_enabled: bool = True
    llm_enabled: bool = False


class OccupancyBody(BaseModel):
    hospital: str
    resource: str
    occupied: int


class FairnessBody(BaseModel):
    enabled: bool


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    page = STATIC / "console.html"
    if not page.exists():
        return "<h1>console.html is missing from mahros/server/static/</h1>"
    return page.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# REST
# --------------------------------------------------------------------------- #

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
def reset(body: ResetBody):
    global NET
    if body.scenario not in SCENARIOS:
        return JSONResponse({"error": f"unknown scenario {body.scenario}"}, 400)
    NET = LiveNetwork(
        scenario=body.scenario,
        seed=body.seed,
        fairness_enabled=body.fairness_enabled,
        llm_enabled=body.llm_enabled,
    )
    NET.seed_load(body.load)
    return NET.state()


@app.post("/api/occupancy")
def set_occupancy(body: OccupancyBody):
    try:
        return NET.set_occupancy(body.hospital, body.resource, body.occupied)
    except (KeyError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, 400)


@app.post("/api/fairness")
def set_fairness(body: FairnessBody):
    return NET.set_fairness(body.enabled)


@app.post("/api/tamper")
def tamper():
    return NET.tamper()


@app.get("/api/ledger")
def ledger():
    return NET.ledger_state()


@app.post("/api/transfer")
def transfer(body: TransferBody):
    """Synchronous transfer, for scripting and for the REST docs."""
    try:
        return NET.request_transfer(
            origin=body.origin,
            resource=body.resource,
            specialty=body.specialty,
            acuity=body.acuity,
            patient_name=body.patient_name,
            patient_note=body.patient_note,
        )
    except (KeyError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, 400)


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


async def stream_negotiation(result: dict, pace_ms: int) -> None:
    """Replay a completed negotiation step by step, paced for a human."""
    pace = max(0, min(pace_ms, 3000)) / 1000.0

    await hub.send_all({"type": "negotiation_start",
                        "request": result["request"],
                        "privacy": result["privacy"]})
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
                            "request_id": result["request"]["request_id"]})
    else:
        await hub.send_all({"type": "failed",
                            "request_id": result["request"]["request_id"],
                            "reason": result.get("failure_reason", "unknown")})

    await asyncio.sleep(pace * 0.5)
    await hub.send_all({"type": "state", "state": NET.state()})


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

            if action == "transfer":
                try:
                    result = NET.request_transfer(
                        origin=msg["origin"],
                        resource=msg.get("resource", "icu_bed"),
                        specialty=msg.get("specialty", "general"),
                        acuity=int(msg.get("acuity", 4)),
                        patient_name=msg.get("patient_name", ""),
                        patient_note=msg.get("patient_note", ""),
                    )
                except (KeyError, ValueError) as exc:
                    await ws.send_text(json.dumps({"type": "error", "message": str(exc)}))
                    continue
                await stream_negotiation(result, int(msg.get("pace_ms", 550)))

            elif action == "auto":
                n = max(1, min(int(msg.get("count", 1)), 25))
                for _ in range(n):
                    result = NET.auto_request()
                    await stream_negotiation(result, int(msg.get("pace_ms", 260)))

            elif action == "state":
                await hub.send_all({"type": "state", "state": NET.state()})

            elif action == "tamper":
                await hub.send_all({"type": "tamper", **NET.tamper()})

            elif action == "fairness":
                NET.set_fairness(bool(msg.get("enabled", True)))
                await hub.send_all({"type": "state", "state": NET.state()})

            elif action == "occupancy":
                try:
                    NET.set_occupancy(msg["hospital"], msg["resource"],
                                      int(msg["occupied"]))
                except (KeyError, ValueError) as exc:
                    await ws.send_text(json.dumps({"type": "error", "message": str(exc)}))
                    continue
                await hub.send_all({"type": "state", "state": NET.state()})

            elif action == "reset":
                globals()["NET"] = LiveNetwork(
                    scenario=msg.get("scenario", "baseline"),
                    seed=int(msg.get("seed", 42)),
                    fairness_enabled=bool(msg.get("fairness_enabled", True)),
                )
                NET.seed_load(float(msg.get("load", 0.62)))
                await hub.send_all({"type": "reset"})
                await hub.send_all({"type": "state", "state": NET.state()})

    except WebSocketDisconnect:
        hub.leave(ws)
    except Exception:
        hub.leave(ws)
