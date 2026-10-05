"""Real road travel times between real hospitals.

Why this exists
---------------
Travel time is the single most decision-relevant number in the whole model. It
decides which bids are feasible at all -- a bid that cannot deliver the patient
inside the clinical safe window is discarded before scoring, never traded off --
so an error here does not shade a result, it changes which hospital wins.

The model previously used straight-line distance at a blended 50 km/h. That is
two assumptions stacked: that ambulances travel in straight lines, and that they
average 50 km/h doing it. Both are now replaced by a routed measurement over the
actual road network.

What the real data showed
-------------------------
Across the 27 Houston facilities, road distance is a median **1.20x** the
straight-line distance -- so the detour assumption was roughly right. But the
*speed* assumption was not: real routed times come out materially shorter than
straight-line-at-50 km/h, because inter-hospital trips in a metro run mostly on
freeways. The old model was over-estimating travel, which made every strategy
look slower than it should.

Source
------
OSRM (Open Source Routing Machine) public demo server, routing over
OpenStreetMap data. ODbL. The computed matrix is vendored so experiments run
offline and reproducibly; re-pull it with
``python experiments/fetch_routes.py``.

Honest limitations
------------------
1. **Free-flow routing.** OSRM's public profile does not model live traffic,
   time of day, or congestion. Real inter-hospital transfer times vary with
   both. This is a better assumption than straight lines, not a measurement of
   what an ambulance actually took.
2. **No blue-light privilege.** An emergency transfer can exceed normal traffic
   speed. Not modelled, and it would shorten times further.
3. **Handover overhead remains an assumption** -- see ``HANDOVER_MINUTES``.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"

#: OSRM public demo server. Rate-limited and not for production load; we call it
#: once per network and vendor the result.
OSRM_TABLE = "https://router.project-osrm.org/table/v1/driving/"

#: Fixed minutes added to every inter-hospital journey, covering packaging the
#: patient at the sending end and crew handover at both ends. This is why very
#: short hops are not free.
#:
#: Still an **assumption**: no public dataset reports door-in-door-out overhead
#: per transfer. Published DIDO targets for time-critical transfers are the
#: nearest anchor. Swept in sensitivity analysis.
HANDOVER_MINUTES = 12.0


def routes_path(metro: str) -> Path:
    return DATA_DIR / f"us_{metro}_metro_routes.json"


def fetch_road_matrix(facilities: list[dict], timeout: float = 180.0) -> dict[str, Any]:
    """Pull an all-pairs driving-time matrix from OSRM.

    `facilities` are the vendored facility records, which already carry real
    geocoded coordinates. Returns durations in seconds and distances in metres,
    indexed positionally to match the input order.
    """
    coords = ";".join(f"{f['lon']:.6f},{f['lat']:.6f}" for f in facilities)
    url = f"{OSRM_TABLE}{coords}?annotations=duration,distance"
    req = urllib.request.Request(url, headers={"User-Agent": "MAHROS research"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode())
    if payload.get("code") != "Ok":
        raise RuntimeError(f"OSRM returned {payload.get('code')}: "
                           f"{payload.get('message')}")
    return payload


def build_routes_payload(metro: str, facilities: list[dict]) -> dict[str, Any]:
    payload = fetch_road_matrix(facilities)
    ccns = [f["ccn"] for f in facilities]
    durations = payload["durations"]
    distances = payload.get("distances")

    missing = sum(1 for row in durations for v in row if v is None)
    return {
        "source": {
            "router": "OSRM public demo server",
            "profile": "driving (free-flow, no live traffic)",
            "network": "OpenStreetMap",
            "license": "ODbL (OpenStreetMap contributors)",
            "note": "Free-flow driving times. Does not model congestion, time "
                    "of day, or emergency-vehicle privilege. Better than "
                    "straight-line distance; not a record of any real journey.",
        },
        "metro": metro,
        "handover_minutes": HANDOVER_MINUTES,
        "handover_note": "Assumed. Added to every inter-hospital journey for "
                         "patient packaging and crew handover at both ends. "
                         "Not observed; swept in sensitivity analysis.",
        "ccns": ccns,
        "unroutable_pairs": missing,
        "durations_seconds": durations,
        "distances_metres": distances,
    }


def write_routes(metro: str, facilities: list[dict]) -> Path:
    payload = build_routes_payload(metro, facilities)
    out = routes_path(metro)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload), encoding="utf-8")
    return out


def load_routes(metro: str) -> dict[str, Any] | None:
    """Load the vendored road matrix, or None if it has not been fetched.

    Returning None rather than raising is deliberate: a network without a
    vendored route matrix should still run, on the straight-line fallback, with
    the substitution reported rather than crashing the experiment.
    """
    target = routes_path(metro)
    if not target.exists():
        return None
    return json.loads(target.read_text(encoding="utf-8"))


def road_travel_minutes(metro: str) -> dict[tuple[str, str], float] | None:
    """``{(ccn_a, ccn_b): minutes}`` including handover, or None if unavailable."""
    payload = load_routes(metro)
    if payload is None:
        return None
    ccns = payload["ccns"]
    durations = payload["durations_seconds"]
    handover = payload.get("handover_minutes", HANDOVER_MINUTES)

    out: dict[tuple[str, str], float] = {}
    for i, a in enumerate(ccns):
        for j, b in enumerate(ccns):
            if i == j:
                out[(a, b)] = 0.0
                continue
            seconds = durations[i][j]
            if seconds is None:
                continue          # caller falls back for this pair
            out[(a, b)] = handover + seconds / 60.0
    return out
