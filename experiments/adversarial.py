"""The headline experiment: what happens when hospitals are not honest.

    python experiments/adversarial.py            # 8 seeds, ~3 min
    python experiments/adversarial.py 20         # 20 seeds

Three questions, in the order a reviewer will ask them.

1. **Does strategic refusal actually damage the network?**
   Sweep the fraction of hospitals that fabricate refusals, with the
   deliberation phase off. This is plain Contract Net, and it is the honest
   statement of the problem: if the damage is small, the mechanism is not
   needed and the paper should say so.

2. **Does ledger-backed argumentation recover the loss?**
   Same sweep with deliberation on, paired seed for seed, tested with a paired
   t-test rather than eyeballed.

3. **Does it ever punish a hospital that was telling the truth?**
   The `defensive` arm is honest but cautious: it refuses more than the norm
   and every refusal is genuine. A mechanism that overrules those is not
   detecting deceit, it is penalising prudence. This is the arm that decides
   whether the mechanism is deployable, and it is reported whatever it says.

Adversary strength is a declared assumption, not a tuned one: the sweep covers
both *how many* hospitals shirk and *how readily* they do it, and every cell is
printed.
"""

from __future__ import annotations

import copy
import json
import statistics as st
import sys
import time
from pathlib import Path

from mahros.eval import paired_t
from mahros.sim import metrics as M
from mahros.sim.runner import RunConfig, SimulationRunner
from mahros.sim.scenario import SCENARIOS

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
SCENARIO = "surge_scarcity"

FRACTIONS = [0.0, 0.25, 0.5, 0.75, 1.0]
SHIRK_THRESHOLDS = [0.30, 0.55, 0.80]   # how readily a strategic hospital lies


def run_one(seed: int, **kw):
    scenario = copy.deepcopy(SCENARIOS[SCENARIO])
    scenario.seed = seed
    result = SimulationRunner(
        RunConfig(scenario=scenario, strategy="mahros", seed=seed, **kw)).run()
    return result, M.compute(result)


def _agg(results) -> dict:
    metrics = [m for _, m in results]
    stats = [r.strategy_stats for r, _ in results]
    return {
        "success_rate": st.fmean(m.success_rate for m in metrics),
        "breach_rate": st.fmean(m.breach_rate for m in metrics),
        "mean_wait": st.fmean(m.mean_wait for m in metrics),
        "critical_breach_rate": st.fmean(m.critical_breach_rate for m in metrics),
        "misreports": st.fmean(s.get("misreports", 0) for s in stats),
        "challenges": st.fmean(s.get("challenges_raised", 0) for s in stats),
        "overruled": st.fmean(s.get("refusals_overruled", 0) for s in stats),
        "false_accusations": st.fmean(
            sum(h.refusals_overruled for h in r.hospitals.values()
                if h.policy.name != "strategic")
            for r, _ in results),
    }


def main(n_seeds: int = 8) -> int:
    t0 = time.perf_counter()
    seeds = [42 + i * 101 for i in range(n_seeds)]
    out: dict = {"meta": {"scenario": SCENARIO, "seeds": seeds,
                          "n_seeds": n_seeds}, "sweeps": {}}

    # -- 1 & 2: dose-response, with and without deliberation --------------- #
    print(f"\n{'=' * 78}\nDOSE-RESPONSE: strategic hospitals vs the argumentation layer")
    print(f"scenario={SCENARIO}, {n_seeds} seeds, adversary threshold 0.55\n")
    print(f"{'strategic':>10} {'deliberation':>13} {'success':>9} {'breach':>8} "
          f"{'wait':>7} {'lies':>7} {'overruled':>10} {'false acc':>10}")

    curve = {}
    for frac in FRACTIONS:
        row = {}
        for argue in (False, True):
            runs = [run_one(s, strategic_fraction=frac,
                            enable_argumentation=argue) for s in seeds]
            agg = _agg(runs)
            agg["_success_by_seed"] = [m.success_rate for _, m in runs]
            row["on" if argue else "off"] = agg
            print(f"{frac:>10.0%} {('ON' if argue else 'off'):>13} "
                  f"{agg['success_rate']:>9.1%} {agg['breach_rate']:>8.1%} "
                  f"{agg['mean_wait']:>7.1f} {agg['misreports']:>7.0f} "
                  f"{agg['overruled']:>10.1f} {agg['false_accusations']:>10.1f}")
        curve[f"{frac:.2f}"] = row
    out["sweeps"]["dose_response"] = curve

    # -- paired test at each dose ------------------------------------------ #
    print(f"\n{'-' * 78}\nPaired test: does deliberation recover what deceit costs?\n")
    baseline = curve["0.00"]["off"]["success_rate"]
    tests = {}
    for frac in FRACTIONS:
        if frac == 0.0:
            continue
        on = curve[f"{frac:.2f}"]["on"]["_success_by_seed"]
        off = curve[f"{frac:.2f}"]["off"]["_success_by_seed"]
        res = paired_t(on, off, label=f"{frac:.0%} strategic: ON - OFF")
        print(res.line())
        lost = baseline - st.fmean(off)
        recovered = res.mean_difference
        print(f"       lost to deceit: {lost:+.1%}   recovered by argument: "
              f"{recovered:+.1%}   ({recovered / lost:.0%} of the loss)"
              if lost > 0.001 else "       (no measurable loss at this dose)")
        tests[f"{frac:.2f}"] = res.as_dict()
    out["paired_tests"] = tests

    # -- 3: false accusation against honest-but-cautious hospitals --------- #
    print(f"\n{'-' * 78}\nSAFETY: is a cautious but honest hospital ever overruled?\n")
    safety = {}
    for label, kw in (
        ("all honest", {}),
        ("50% defensive", {"defensive_fraction": 0.5}),
        ("25% strategic + 25% defensive",
         {"strategic_fraction": 0.25, "defensive_fraction": 0.25}),
    ):
        runs = [run_one(s, enable_argumentation=True, **kw) for s in seeds]
        agg = _agg(runs)
        safety[label] = agg
        print(f"  {label:<32s} challenges={agg['challenges']:>6.0f}  "
              f"overruled={agg['overruled']:>5.1f}  "
              f"FALSE ACCUSATIONS={agg['false_accusations']:>4.1f}")
    out["safety"] = {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
                     for k, v in safety.items()}

    # -- adversary-strength sensitivity ------------------------------------ #
    print(f"\n{'-' * 78}\nSENSITIVITY: how much does adversary aggression matter?\n")
    print(f"{'threshold':>10} {'delib off':>11} {'delib on':>10} {'recovered':>11}")
    sens = {}
    for thresh in SHIRK_THRESHOLDS:
        off = [run_one(s, strategic_fraction=0.5, shirk_comfort_threshold=thresh,
                       enable_argumentation=False) for s in seeds]
        on = [run_one(s, strategic_fraction=0.5, shirk_comfort_threshold=thresh,
                      enable_argumentation=True) for s in seeds]
        a, b = _agg(off)["success_rate"], _agg(on)["success_rate"]
        sens[f"{thresh:.2f}"] = {"off": a, "on": b}
        print(f"{thresh:>10.2f} {a:>11.1%} {b:>10.1%} {b - a:>+11.1%}")
    out["sweeps"]["adversary_strength"] = sens

    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / "adversarial.json"
    path.write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
    print(f"\nwrote {path.relative_to(ROOT)}  ({time.perf_counter() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 8))
