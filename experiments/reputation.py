"""Risk-based spot checks: check the hospitals that have already been caught.

    python experiments/reputation.py            # 6 seeds
    python experiments/reputation.py 10

`collusion.py` shows that proactive spot checks are the one defence a
coordinated cartel cannot dodge, but it checks every capacity refusal at the
same rate. The protocol has no memory: a hospital caught misreporting is
treated exactly like one that never has been.

Here a hospital that has failed a spot check, or had a refusal overruled, is
checked at a higher rate from then on. Honest hospitals sign true claims and
so can never be caught; escalation can only land on a proven misreporter.

The comparison that matters is at **equal checking effort**: a scheme that
simply checks more will do better, so every arm reports how many checks it
spent and the false accusations it made.
"""

from __future__ import annotations

import copy
import json
import statistics as st
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mahros.eval import paired_t  # noqa: E402
from mahros.sim import metrics as M  # noqa: E402
from mahros.sim.runner import RunConfig, SimulationRunner  # noqa: E402
from mahros.sim.scenario import SCENARIOS  # noqa: E402

RESULTS = ROOT / "results"
SCENARIO = "houston_surge"
CARTEL = 6
ATTEST = dict(enable_attestation=True, attestation_deterrence=0.8)

#: (label, base rate, rate once caught)
ARMS = [
    ("no spot checks", 0.0, 0.0),
    ("uniform 5%", 0.05, 0.0),
    ("uniform 10%", 0.10, 0.0),
    ("uniform 15%", 0.15, 0.0),
    ("5%, 50% once caught", 0.05, 0.50),
    ("5%, 100% once caught", 0.05, 1.00),
]


def run_one(seed: int, collusion_size: int, base: float, escalated: float):
    sc = copy.deepcopy(SCENARIOS[SCENARIO])
    sc.seed = seed
    r = SimulationRunner(RunConfig(
        scenario=sc, strategy="mahros", seed=seed, collusion_size=collusion_size,
        spot_check_rate=base, spot_check_escalated_rate=escalated, **ATTEST)).run()
    return r, M.compute(r)


def summarise(runs) -> dict:
    return {
        "success_rate": st.fmean(m.success_rate for _, m in runs),
        "success_by_seed": [m.success_rate for _, m in runs],
        "spot_checks": st.fmean(r.strategy_stats.get("spot_checks", 0) for r, _ in runs),
        "cartel_overruled": st.fmean(
            sum(h.refusals_overruled for h in r.hospitals.values()
                if h.policy.name == "colluding") for r, _ in runs),
        "false_accusations": st.fmean(
            sum(h.refusals_overruled for h in r.hospitals.values()
                if h.policy.name not in ("strategic", "colluding")) for r, _ in runs),
    }


def main(n_seeds: int = 6) -> int:
    t0 = time.perf_counter()
    seeds = [42 + i * 101 for i in range(n_seeds)]
    out: dict = {"meta": {"scenario": SCENARIO, "cartel": CARTEL, "seeds": seeds,
                          "attestation_deterrence": ATTEST["attestation_deterrence"]},
                 "arms": {}}
    print(f"Risk-based spot checks: cartel of {CARTEL} on {SCENARIO}, {n_seeds} seeds\n")
    print(f"{'arm':<24}{'honest net':>11}{'cartel':>9}{'checks':>9}"
          f"{'cartel caught':>15}{'false acc.':>12}")
    for label, base, esc in ARMS:
        honest = summarise([run_one(s, 0, base, esc) for s in seeds])
        cartel = summarise([run_one(s, CARTEL, base, esc) for s in seeds])
        out["arms"][label] = {"base_rate": base, "escalated_rate": esc,
                              "honest": honest, "cartel": cartel}
        print(f"{label:<24}{honest['success_rate']:>11.1%}{cartel['success_rate']:>9.1%}"
              f"{cartel['spot_checks']:>9.0f}{cartel['cartel_overruled']:>15.1f}"
              f"{cartel['false_accusations'] + honest['false_accusations']:>12.1f}")

    # The question: at no more checking than uniform 10-15%, does targeting do better?
    ref = out["arms"]["uniform 15%"]["cartel"]
    for label in ("5%, 50% once caught", "5%, 100% once caught"):
        arm = out["arms"][label]["cartel"]
        t = paired_t(arm["success_by_seed"], ref["success_by_seed"])
        out["arms"][label]["vs_uniform_15"] = {
            "diff": t.mean_difference, "p": t.p_value,
            "checks_ratio": arm["spot_checks"] / ref["spot_checks"] if ref["spot_checks"] else None}
        print(f"\n{label} vs uniform 15%: success {t.mean_difference * 100:+.2f} pts "
              f"(p = {t.p_value:.4f}) using {arm['spot_checks'] / ref['spot_checks']:.0%} "
              f"of the checks" if ref["spot_checks"] else "")

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "reputation.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote results/reputation.json  ({time.perf_counter() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 6))
