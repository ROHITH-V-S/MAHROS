"""Fetch and vendor real road travel times between the real hospitals.

    python experiments/fetch_routes.py --metro houston
    python experiments/fetch_routes.py --compare      # road vs straight-line

Vendored so every experiment runs offline. Kept in the repo so the matrix is
reproducible rather than asserted.

Source: OSRM public demo server over OpenStreetMap (ODbL). Free-flow driving
times -- no live traffic, no emergency-vehicle privilege.
"""

from __future__ import annotations

import argparse
import math
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mahros.calibration.facilities import METROS, load_vendor  # noqa: E402
from mahros.calibration.routing import (  # noqa: E402
    HANDOVER_MINUTES,
    load_routes,
    road_travel_minutes,
    routes_path,
    write_routes,
)

#: The straight-line model being replaced, kept here so the comparison is
#: explicit rather than remembered.
LEGACY_SPEED_KMH = 50.0


def compare(metro: str) -> None:
    payload = load_vendor(metro)
    facs = payload["facilities"]
    routes = road_travel_minutes(metro)
    if routes is None:
        print(f"no vendored routes for {metro}; run without --compare first")
        return

    sel = payload["selection"]
    km_lat = 110.574
    km_lon = 111.320 * math.cos(math.radians(sel["centroid_lat"]))

    road, legacy, detour = [], [], []
    for a in facs:
        for b in facs:
            if a["ccn"] == b["ccn"]:
                continue
            key = (a["ccn"], b["ccn"])
            if key not in routes:
                continue
            ax = (a["lon"] - sel["centroid_lon"]) * km_lon
            ay = (a["lat"] - sel["centroid_lat"]) * km_lat
            bx = (b["lon"] - sel["centroid_lon"]) * km_lon
            by = (b["lat"] - sel["centroid_lat"]) * km_lat
            straight_km = math.hypot(ax - bx, ay - by)
            road.append(routes[key])
            legacy.append(HANDOVER_MINUTES + (straight_km / LEGACY_SPEED_KMH) * 60.0)

    rp = load_routes(metro)
    dists = rp.get("distances_metres")
    if dists:
        for i, a in enumerate(facs):
            for j, b in enumerate(facs):
                if i == j or dists[i][j] is None:
                    continue
                ax = (a["lon"] - sel["centroid_lon"]) * km_lon
                ay = (a["lat"] - sel["centroid_lat"]) * km_lat
                bx = (b["lon"] - sel["centroid_lon"]) * km_lon
                by = (b["lat"] - sel["centroid_lat"]) * km_lat
                s = math.hypot(ax - bx, ay - by)
                if s > 1.0:
                    detour.append((dists[i][j] / 1000.0) / s)

    print(f"\n{len(road)} routed hospital pairs\n")
    print(f"  {'':<26}{'min':>8}{'median':>9}{'mean':>8}{'max':>8}")
    print(f"  {'road (routed + handover)':<26}{min(road):>8.1f}{st.median(road):>9.1f}"
          f"{st.fmean(road):>8.1f}{max(road):>8.1f}")
    print(f"  {'straight line @ 50 km/h':<26}{min(legacy):>8.1f}{st.median(legacy):>9.1f}"
          f"{st.fmean(legacy):>8.1f}{max(legacy):>8.1f}")
    if detour:
        print(f"\n  road/straight-line distance ratio: median "
              f"{st.median(detour):.2f}  (the detour factor)")
    diff = st.fmean(road) - st.fmean(legacy)
    print(f"\n  mean difference: {diff:+.1f} min per transfer")
    if diff < 0:
        print("  -> the straight-line model was OVER-estimating travel time.")
        print("     Detours are real (1.2x distance) but metro inter-hospital")
        print("     trips run mostly on freeways, well above a blended 50 km/h.")
    print("\n  Every strategy uses the same matrix, so comparisons between them")
    print("  are unaffected; absolute times shift.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--metro", default="houston", choices=sorted(METROS))
    ap.add_argument("--compare", action="store_true",
                    help="compare vendored road times against straight-line")
    args = ap.parse_args()

    if args.compare:
        compare(args.metro)
        return 0

    payload = load_vendor(args.metro)
    facs = payload["facilities"]
    print(f"routing {len(facs)}x{len(facs)} hospital pairs via OSRM ...")
    out = write_routes(args.metro, facs)
    rp = load_routes(args.metro)
    print(f"wrote {out.relative_to(ROOT)}  ({out.stat().st_size / 1024:.0f} KB)")
    if rp["unroutable_pairs"]:
        print(f"  WARNING: {rp['unroutable_pairs']} pairs unroutable; those fall "
              f"back to straight-line")
    compare(args.metro)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
