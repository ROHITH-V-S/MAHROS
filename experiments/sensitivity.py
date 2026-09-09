"""Sweep every parameter the model could be accused of choosing conveniently.

    python experiments/sensitivity.py           # 5 seeds, ~6 min
    python experiments/sensitivity.py 10

`docs/MODEL_ASSUMPTIONS.md` §8 lists the values with no published source and
says each one must appear in a sensitivity analysis. This is that analysis. It
also sweeps the two adversary parameters, because the strength of the attack
must never be a free variable chosen to flatter the defence.

The claim under test is deliberately weak and therefore defensible: not that the
absolute numbers are right, but that the **ranking of strategies survives**
across the plausible range of every unsourced parameter. Where a ranking flips,
this script prints it, and the paper must demote that claim.
"""

from __future__ import annotations

import copy
import json
import statistics as st
import sys
import time
from pathlib import Path

from mahros.hospital.behaviours import StrategicPolicy
from mahros.negotiation.cnp import CNPConfig
from mahros.sim import metrics as M
from mahros.sim.runner import RunConfig, SimulationRunner
from mahros.sim.scenario import SCENARIOS
from mahros.sim.strategies import PhoneTreeStrategy

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
SCENARIO = "surge_scarcity"


def run(strategy: str, seed: int, **kw) -> float:
    scenario = copy.deepcopy(SCENARIOS[SCENARIO])
    scenario.seed = seed
    result = SimulationRunner(
        RunConfig(scenario=scenario, strategy=strategy, seed=seed, **kw)).run()
    return M.compute(result).success_rate


def mean(strategy: str, seeds: list[int], **kw) -> float:
    return st.fmean(run(strategy, s, **kw) for s in seeds)


def section(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def main(n_seeds: int = 8) -> int:
    t0 = time.perf_counter()
    seeds = [42 + i * 101 for i in range(n_seeds)]
    out: dict = {"meta": {"scenario": SCENARIO, "seeds": seeds}, "sweeps": {}}
    flips: list[str] = []

    # -- 1. clinical safe windows ----------------------------------------- #
    # The most consequential assumption in the model: absolute success rates
    # move a great deal with these, which is exactly why the paper's claims are
    # about the ranking rather than the levels.
    section("1. Clinical safe windows  (scale all acuity windows)")
    from mahros.core import types as T

    original = T.Acuity.safe_window_minutes.fget
    print(f"{'scale':>7} {'mahros':>9} {'phone':>9} {'central':>9} {'ranking holds':>15}")
    windows = {}
    for scale in (0.7, 0.85, 1.0, 1.25, 1.5):
        T.Acuity.safe_window_minutes = property(
            lambda self, s=scale: int(original(self) * s))
        vals = {k: mean(k, seeds) for k in ("mahros", "phone", "central")}
        holds = vals["mahros"] > vals["phone"]
        windows[f"{scale:.2f}"] = vals
        print(f"{scale:>7.2f} {vals['mahros']:>9.1%} {vals['phone']:>9.1%} "
              f"{vals['central']:>9.1%} {('yes' if holds else 'NO — FLIPPED'):>15}")
        if not holds:
            flips.append(f"safe-window scale {scale}")
    T.Acuity.safe_window_minutes = property(original)
    out["sweeps"]["safe_window_scale"] = windows

    # -- 2. phone-tree staleness (the least defensible number) ------------- #
    section("2. Phone-tree staleness  (unsourced; drives the headline gap)")
    baseline = mean("mahros", seeds)
    print(f"MAHROS reference: {baseline:.1%}")
    print(f"{'staleness':>10} {'phone':>9} {'MAHROS wins':>13}")
    stale = {}
    keep = PhoneTreeStrategy.STALE_INFO_PROB
    for prob in (0.0, 0.05, 0.10, 0.25, 0.40):
        PhoneTreeStrategy.STALE_INFO_PROB = prob
        val = mean("phone", seeds)
        stale[f"{prob:.2f}"] = val
        holds = baseline > val
        print(f"{prob:>10.2f} {val:>9.1%} {('yes' if holds else 'NO — FLIPPED'):>13}")
        if not holds:
            flips.append(f"phone staleness {prob}")
    PhoneTreeStrategy.STALE_INFO_PROB = keep
    out["sweeps"]["phone_staleness"] = stale

    # -- 3. phone-tree call duration --------------------------------------- #
    section("3. Phone-tree call duration  (unsourced)")
    print(f"{'minutes':>8} {'phone':>9} {'MAHROS wins':>13}")
    calls = {}
    keep = PhoneTreeStrategy.MEAN_CALL_MINUTES
    for minutes in (5.0, 7.0, 9.0, 12.0, 15.0):
        PhoneTreeStrategy.MEAN_CALL_MINUTES = minutes
        val = mean("phone", seeds)
        calls[f"{minutes:.0f}"] = val
        holds = baseline > val
        print(f"{minutes:>8.0f} {val:>9.1%} {('yes' if holds else 'NO — FLIPPED'):>13}")
        if not holds:
            flips.append(f"call duration {minutes}")
    PhoneTreeStrategy.MEAN_CALL_MINUTES = keep
    out["sweeps"]["call_duration"] = calls

    # -- 4. MAHROS's own staff-time cost ----------------------------------- #
    # Swept because it sets the size of MAHROS's headline saving, and a
    # reviewer is entitled to assume we picked it generously.
    section("4. MAHROS agent coordination cost  (unsourced; our own advantage)")
    print(f"{'min/request':>12} {'mahros':>9} {'coord min/transfer':>20}")
    coord = {}
    keep = CNPConfig.agent_coordination_minutes
    for minutes in (0.25, 0.5, 1.0, 2.0):
        CNPConfig.agent_coordination_minutes = minutes
        scenario = copy.deepcopy(SCENARIOS[SCENARIO])
        scenario.seed = seeds[0]
        res = SimulationRunner(
            RunConfig(scenario=scenario, strategy="mahros", seed=seeds[0])).run()
        met = M.compute(res)
        coord[f"{minutes:.2f}"] = {
            "success": met.success_rate,
            "coordinator_minutes": met.coordinator_minutes_per_transfer,
        }
        print(f"{minutes:>12.2f} {met.success_rate:>9.1%} "
              f"{met.coordinator_minutes_per_transfer:>20.1f}")
    CNPConfig.agent_coordination_minutes = keep
    out["sweeps"]["agent_coordination_cost"] = coord

    # -- 5. deliberation overhead ------------------------------------------ #
    section("5. Deliberation round-trip cost  (the new layer's own price)")
    print(f"{'minutes':>8} {'honest net':>11} {'50% strategic':>14}")
    delib = {}
    keep = CNPConfig.deliberation_minutes
    for minutes in (0.0, 0.75, 2.0, 5.0):
        CNPConfig.deliberation_minutes = minutes
        clean = mean("mahros", seeds)
        attacked = mean("mahros", seeds, strategic_fraction=0.5)
        delib[f"{minutes:.2f}"] = {"honest": clean, "adversarial": attacked}
        print(f"{minutes:>8.2f} {clean:>11.1%} {attacked:>14.1%}")
    CNPConfig.deliberation_minutes = keep
    out["sweeps"]["deliberation_cost"] = delib

    # -- 6. adversary strength --------------------------------------------- #
    section("6. Adversary strength  (declared, never tuned to flatter the defence)")
    print(f"{'threshold':>10} {'shirk p':>8} {'delib off':>11} {'delib on':>10} {'gain':>8}")
    adversary = {}
    for threshold in (0.30, 0.55, 0.80):
        for shirk in (0.5, 1.0):
            off = mean("mahros", seeds, strategic_fraction=0.5,
                       shirk_comfort_threshold=threshold, shirk_prob=shirk,
                       enable_argumentation=False)
            on = mean("mahros", seeds, strategic_fraction=0.5,
                      shirk_comfort_threshold=threshold, shirk_prob=shirk,
                      enable_argumentation=True)
            adversary[f"t{threshold}_p{shirk}"] = {"off": off, "on": on}
            print(f"{threshold:>10.2f} {shirk:>8.1f} {off:>11.1%} {on:>10.1%} "
                  f"{on - off:>+8.1%}")
            if on < off:
                flips.append(f"argumentation harmful at threshold={threshold}, shirk={shirk}")
    out["sweeps"]["adversary_strength"] = adversary

    # -- verdict ----------------------------------------------------------- #
    section("VERDICT")
    if flips:
        print("Rankings that did NOT survive — these claims must be demoted:")
        for f in flips:
            print(f"  * {f}")
    else:
        print("Every ranking survived every sweep.")
        print("  MAHROS > phone tree across all safe-window scales, all staleness")
        print("  values, and all call durations tested.")
        print("  Deliberation never made the adversarial case worse.")
    out["ranking_flips"] = flips

    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / "sensitivity.json"
    path.write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
    print(f"\nwrote {path.relative_to(ROOT)}  ({time.perf_counter() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 8))
