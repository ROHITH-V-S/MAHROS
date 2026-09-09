"""Paired significance and equivalence tests for every headline claim.

    python experiments/significance.py          # 12 seeds
    python experiments/significance.py 30       # journal-grade

Named `significance.py`, not `statistics.py`, because a module of the latter
name in this directory shadows the standard library one that `mahros.eval`
imports. A small thing that costs an hour if you meet it cold.

Kept separate from `run_all.py` because it answers a different question. That
script asks *what the numbers are*; this one asks *which differences you are
entitled to claim*.

Two instruments, and the distinction matters:

  **Paired t-test** — for claiming a difference. Pairing is over common random
  numbers: each seed produces matched runs on the same patient stream, so the
  large seed-to-seed variance cancels instead of drowning the effect.

  **TOST equivalence** — for claiming *no* difference. A non-significant t-test
  does not show two systems perform the same; it is equally consistent with too
  few seeds or a noisy metric. TOST inverts the burden of proof and asks whether
  the difference is demonstrably *smaller* than a margin declared in advance.

The margin is declared here, before any result: two percentage points of
transfer success rate, which is below the fortnight-to-fortnight variation a
commissioner would see anyway.
"""

from __future__ import annotations

import copy
import json
import statistics as st
import sys
import time
from pathlib import Path

from mahros.eval import bootstrap_ci, holm_adjust, paired_t, tost_equivalence
from mahros.sim import metrics as M
from mahros.sim.runner import RunConfig, SimulationRunner
from mahros.sim.scenario import SCENARIOS

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"

SCENARIO = "surge_scarcity"
#: Declared before the analysis. See module docstring.
EQUIVALENCE_MARGIN = 0.02


def series(strategy: str, seeds: list[int], **kw) -> dict[str, list[float]]:
    """Per-seed metric vectors for one arm."""
    out = {"success": [], "wait": [], "breach": [], "gini": []}
    for seed in seeds:
        scenario = copy.deepcopy(SCENARIOS[SCENARIO])
        scenario.seed = seed
        met = M.compute(SimulationRunner(
            RunConfig(scenario=scenario, strategy=strategy, seed=seed, **kw)).run())
        out["success"].append(met.success_rate)
        out["wait"].append(met.mean_wait)
        out["breach"].append(met.breach_rate)
        out["gini"].append(met.burden_gini)
    return out


def main(n_seeds: int = 40) -> int:
    t0 = time.perf_counter()
    seeds = [42 + i * 101 for i in range(n_seeds)]
    out: dict = {"meta": {"scenario": SCENARIO, "n_seeds": n_seeds, "seeds": seeds,
                          "equivalence_margin": EQUIVALENCE_MARGIN}}

    print(f"\n{'=' * 78}")
    print(f"STATISTICS  scenario={SCENARIO}  {n_seeds} paired seeds")
    print(f"equivalence margin declared in advance: "
          f"{EQUIVALENCE_MARGIN:.0%} of transfer success rate")
    print("=" * 78)

    arms = {name: series(name, seeds) for name in
            ("mahros", "phone", "central", "optimal", "nearest")}
    arms["mahros_no_fairness"] = series("mahros", seeds, fairness_enabled=False)
    arms["mahros_no_argument"] = series("mahros", seeds, enable_argumentation=False)

    # -- 1. differences we do claim ---------------------------------------- #
    print("\n1. DIFFERENCES  (paired t-test on transfer success rate)\n")
    claims = {}
    for other in ("phone", "central", "optimal", "nearest"):
        res = paired_t(arms["mahros"]["success"], arms[other]["success"],
                       label=f"MAHROS - {other}")
        claims[f"mahros_vs_{other}"] = res
        print(res.line())

    adjusted = holm_adjust({k: v.p_value for k, v in claims.items()})
    print("\n   Holm-adjusted p-values (four comparisons on the same runs):")
    for key, val in sorted(adjusted.items(), key=lambda kv: kv[1]):
        verdict = "significant" if val < 0.05 else "not significant"
        print(f"     {key:<26s} p_adj={val:.4f}  {verdict}")
    out["differences"] = {k: v.as_dict() for k, v in claims.items()}
    out["holm_adjusted"] = adjusted

    # -- 2. equivalences we do claim --------------------------------------- #
    print("\n2. EQUIVALENCE  (TOST -- for claiming two arms perform the same)\n")
    equivs = {}
    for other in ("central", "optimal"):
        res = tost_equivalence(arms["mahros"]["success"], arms[other]["success"],
                               margin=EQUIVALENCE_MARGIN, label=f"MAHROS vs {other}")
        equivs[f"equivalence_vs_{other}"] = res
        print(res.line())
    out["equivalence"] = {k: v.as_dict() for k, v in equivs.items()}

    # -- 3. the null result, reported as a null result --------------------- #
    print("\n3. THE FAIRNESS LAYER  (reported as measured, not as hoped)\n")
    fair = {}
    for metric, label in (("gini", "burden Gini"), ("success", "success rate"),
                          ("wait", "mean wait")):
        res = paired_t(arms["mahros"][metric], arms["mahros_no_fairness"][metric],
                       label=f"fairness ON - OFF ({label})")
        fair[metric] = res
        print(res.line())
    point, lo, hi = bootstrap_ci(
        [a - b for a, b in zip(arms["mahros"]["gini"],
                               arms["mahros_no_fairness"]["gini"])])
    print(f"   bootstrap CI on the Gini difference: {point:+.4f} [{lo:+.4f}, {hi:+.4f}]")
    print("   -> If this interval spans zero, the fairness layer has no detectable")
    print("      effect at this sample size. Say so in the paper.")
    out["fairness"] = {k: v.as_dict() for k, v in fair.items()}
    out["fairness"]["gini_bootstrap"] = {"point": point, "lo": lo, "hi": hi}

    # -- 4. the price of the deliberation phase ---------------------------- #
    print("\n4. COST OF DELIBERATION ON AN HONEST NETWORK\n")
    delib = {}
    for metric, label in (("success", "success rate"), ("wait", "mean wait")):
        res = paired_t(arms["mahros"][metric], arms["mahros_no_argument"][metric],
                       label=f"deliberation ON - OFF ({label})")
        delib[metric] = res
        print(res.line())
    print("   -> This is the premium paid for insurance nobody is claiming on.")
    out["deliberation_cost"] = {k: v.as_dict() for k, v in delib.items()}

    # -- summary ------------------------------------------------------------ #
    print(f"\n{'-' * 78}\nWHAT YOU MAY CLAIM\n")
    for key, res in claims.items():
        if adjusted[key] < 0.05:
            direction = "better than" if res.mean_difference > 0 else "worse than"
            print(f"  MAHROS is {direction} {key.split('_vs_')[1]}: "
                  f"{abs(res.mean_difference):.1%} (p_adj={adjusted[key]:.4f})")
    for key, res in equivs.items():
        if res.equivalent:
            print(f"  MAHROS is statistically EQUIVALENT to "
                  f"{key.split('_vs_')[1]} within {EQUIVALENCE_MARGIN:.0%} "
                  f"(p={res.p_value:.4f})")
    if not fair["gini"].significant:
        print("  The fairness layer's effect on burden Gini is NOT significant. "
              "Report as a null result.")

    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / "significance.json"
    path.write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
    print(f"\nwrote {path.relative_to(ROOT)}  ({time.perf_counter() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 40))
