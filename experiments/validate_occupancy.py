"""Does the simulation reproduce the occupancy these hospitals actually ran at?

    python experiments/validate_occupancy.py                     # houston_baseline
    python experiments/validate_occupancy.py houston_surge 5     # scenario, seeds

This is the check the project previously did not have. The old calibration
compared four *aggregate ratios* against summary statistics, after the arrival
rates had already been tuned to hit them. That is a sanity check, not a
validation: it cannot fail in an interesting way.

This script asks a question that can genuinely fail. Each of the 27 hospitals
is a real facility whose inpatient occupancy in the target week is known. The
simulator is given that hospital's real bed and ICU counts and an arrival rate
fitted by Little's Law, then run. The occupancy it *produces* is compared,
facility by facility, against the occupancy that facility *reported*.

Reported as mean absolute error, bias, and R-squared across facilities, plus the
worst individual misses. A poor fit is a result and is printed as one.

What this does and does not establish
-------------------------------------
It establishes that the network's bed dynamics are consistent with real
observed capacity and load. It does **not** validate the negotiation mechanism,
the behaviour model, or the clinical timing assumptions -- none of which this
dataset can speak to. See docs/DATA_PROVENANCE.md for the full split.
"""

from __future__ import annotations

import copy
import json
import math
import statistics as st
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mahros.core.types import ResourceType  # noqa: E402
from mahros.sim.real_network import (  # noqa: E402
    OCCUPANCY_FIT_CEILING,
    build_real_network,
)
from mahros.sim.runner import RunConfig, SimulationRunner  # noqa: E402
from mahros.sim.scenario import SCENARIOS  # noqa: E402

RESULTS = ROOT / "results"

#: Fraction of each run discarded as warm-up. The simulation starts every bed
#: empty; at a ~4.6 day mean stay a 14-day run needs roughly its first third to
#: reach steady state. Declared here rather than tuned.
BURN_IN = 0.35

#: Inpatient beds in the HHS definition are ward + step-down + ICU, so the
#: simulated figure must be pooled over exactly those three to be comparable.
INPATIENT = (ResourceType.WARD_BED, ResourceType.HDU_BED, ResourceType.ICU_BED)


def simulated_occupancy(scenario_name: str, seed: int) -> dict[str, dict[str, float]]:
    sc = copy.deepcopy(SCENARIOS[scenario_name])
    sc.seed = seed
    result = SimulationRunner(
        RunConfig(scenario=sc, strategy="mahros", seed=seed)).run()

    out: dict[str, dict[str, float]] = {}
    for hid, hosp in result.hospitals.items():
        util = hosp.resources.mean_utilisation(burn_in=BURN_IN)
        caps = hosp.cfg.capacities
        total = sum(caps.get(r, 0) for r in INPATIENT)
        if total <= 0:
            continue
        # Capacity-weighted mean across the three inpatient pools.
        occupied = sum(util.get(r, 0.0) * caps.get(r, 0) for r in INPATIENT)
        icu_cap = caps.get(ResourceType.ICU_BED, 0)
        out[hid] = {
            "inpatient": occupied / total,
            "icu": util.get(ResourceType.ICU_BED, 0.0) if icu_cap else float("nan"),
        }
    return out


def _r2(obs: list[float], sim: list[float]) -> float:
    if len(obs) < 2:
        return float("nan")
    mean_obs = st.fmean(obs)
    ss_tot = sum((o - mean_obs) ** 2 for o in obs)
    ss_res = sum((o - s) ** 2 for o, s in zip(obs, sim))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")


def main(scenario_name: str = "houston_baseline", n_seeds: int = 5) -> int:
    t0 = time.perf_counter()
    sc = SCENARIOS[scenario_name]
    if not sc.is_real:
        print(f"{scenario_name} is a synthetic scenario: there is no observed "
              f"occupancy to validate against.")
        return 2

    net = build_real_network(metro=sc.real_metro, week=sc.real_week,
                             capacity_scale=sc.capacity_scale)
    seeds = [42 + i * 101 for i in range(n_seeds)]

    print(f"\nValidating simulated occupancy against reported occupancy")
    print(f"  network : {net.label}, {net.n_hospitals} real hospitals")
    print(f"  week    : {net.week}")
    print(f"  seeds   : {n_seeds}   burn-in: {BURN_IN:.0%}\n")

    per_seed = [simulated_occupancy(scenario_name, s) for s in seeds]

    rows = []
    for hid, obs in net.observed.items():
        sims = [ps[hid]["inpatient"] for ps in per_seed if hid in ps]
        if not sims:
            continue
        sim = st.fmean(sims)
        rows.append({
            "ccn": hid,
            "name": next(c.name for c in net.configs if c.hospital_id == hid),
            "beds": obs["inpatient_beds"],
            "observed": obs["occupancy"],
            # Facilities reporting above the fitting ceiling are fitted to it;
            # error against the reported figure then includes that deliberate
            # gap, so both are carried.
            "fitted_target": obs.get("fitted_occupancy", obs["occupancy"]),
            "capped": obs.get("fitted_occupancy", obs["occupancy"]) < obs["occupancy"] - 1e-9,
            "simulated": sim,
            "error": sim - obs["occupancy"],
            "error_vs_target": sim - obs.get("fitted_occupancy", obs["occupancy"]),
            "sd_across_seeds": st.pstdev(sims) if len(sims) > 1 else 0.0,
        })

    obs_v = [r["observed"] for r in rows]
    sim_v = [r["simulated"] for r in rows]
    errs = [r["error"] for r in rows]
    mae = st.fmean(abs(e) for e in errs)
    bias = st.fmean(errs)
    rmse = math.sqrt(st.fmean(e * e for e in errs))
    r2 = _r2(obs_v, sim_v)

    print(f"  {'hospital':<40}{'beds':>6}{'observed':>10}{'simulated':>11}{'error':>9}")
    for r in sorted(rows, key=lambda r: -abs(r["error"])):
        print(f"  {r['name'][:38]:<40}{r['beds']:>6.0f}"
              f"{r['observed']:>10.3f}{r['simulated']:>11.3f}{r['error']:>+9.3f}")

    capped = [r for r in rows if r["capped"]]
    err_t = [r["error_vs_target"] for r in rows]
    mae_t = st.fmean(abs(e) for e in err_t)

    print(f"\n  {'-' * 72}")
    print(f"  facilities compared      : {len(rows)}")
    print(f"  fitted to ceiling        : {len(capped)} "
          f"(reported occupancy above the declared ceiling)")
    print(f"  MAE vs fitted target     : {mae_t:.4f}  "
          f"({mae_t * 100:.2f} occupancy points)")
    print(f"  mean absolute error      : {mae:.4f}  ({mae * 100:.2f} occupancy points)")
    print(f"  bias (simulated - obs)   : {bias:+.4f}")
    print(f"  RMSE                     : {rmse:.4f}")
    print(f"  R^2 across facilities    : {r2:.3f}")
    print(f"  observed range           : {min(obs_v):.3f} .. {max(obs_v):.3f}")
    print(f"  simulated range          : {min(sim_v):.3f} .. {max(sim_v):.3f}")

    verdict = ("GOOD" if mae < 0.05 else "FAIR" if mae < 0.10 else "POOR")
    print(f"\n  verdict: {verdict} -- mean absolute error {mae * 100:.1f} "
          f"occupancy points across {len(rows)} real facilities")
    if verdict == "POOR":
        print("  Reported as measured. A poor fit here means the arrival "
              "model, not the negotiation mechanism, needs work.")

    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"validate_occupancy_{scenario_name}.json"
    path.write_text(json.dumps({
        "scenario": scenario_name, "metro": net.metro, "week": net.week,
        "n_seeds": n_seeds, "burn_in": BURN_IN, "seeds": seeds,
        "mae": mae, "bias": bias, "rmse": rmse, "r2": r2,
        "mae_vs_fitted_target": mae_t, "n_capped": len(capped),
        "occupancy_fit_ceiling": OCCUPANCY_FIT_CEILING,
        "verdict": verdict, "facilities": rows,
        "source": net.source,
    }, indent=2, default=float), encoding="utf-8")
    print(f"\n  wrote {path.relative_to(ROOT)}  ({time.perf_counter() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "houston_baseline"
    seeds = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    raise SystemExit(main(name, seeds))
