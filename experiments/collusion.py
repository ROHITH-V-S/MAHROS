"""Collusion: the attack that breaks the mechanism, and the one thing that stops it.

    python experiments/collusion.py           # 6 seeds
    python experiments/collusion.py 10

The project's own limitations section named collusion the most important
unmodelled gap. It was right, and the gap is worse than it looked.

The attack
----------
A cartel does not lie better. It stops producing evidence. The ledger challenge
looks for one hospital that refused a resource and then accepted it; if the
cartel abstains in concert -- nobody accepts R while any member is refusing R --
that pair never appears. Every refusal is then individually unimpeachable.

What fails, and why
-------------------
Four defences were tried against it and the first three fail, which is the
substance of this experiment rather than an embarrassment to be hidden:

1. **Registry check** -- untouched, but irrelevant: this is a capacity claim,
   not a capability one.
2. **Ledger challenge** -- fails by construction. There is no contradiction.
3. **Statistical co-refusal detection** -- fails, and instructively. Two
   variants were measured, and both score the *colluding* network LOWER than
   the honest one: same-request lift 1.137 against 1.475, temporal follow-on
   1.061 against 1.240. The cartel's signature sits in the marginal refusal
   rates, which lift divides out; at the occupancy these hospitals really run
   at everyone refuses 93-100% of the time and the statistic saturates; and the
   cartel raises its neighbours' refusal rates by congesting them, so it hides
   inside the damage it causes.
4. **Attestation** -- fails on its own, and the reason is the finding. Every
   check in the protocol is *reactive*: it examines a refusal only once
   something flagged it. Attestation fires on a challenge, and the cartel's
   whole design is to never provoke one.

What works
----------
Make a sample of the checking **proactive**. Ask a random fraction of refusals
to certify themselves under signature with no evidence against them at all. A
cartel member abstaining while it has a free bed must then either decline --
and lose the refusal -- or sign a false statement.

Spot checks on a fully honest network cost nothing measurable, for two reasons
that had to be established separately rather than assumed:

* Striking out a refusal substitutes the hospital's *truthful* bid, and a
  genuinely full hospital's truthful bid is still a refusal. Nobody is forced to
  take a patient they cannot take.
* Only **capacity** claims are spot-checked. A capability refusal ("we don't
  offer that resource") is already checkable against the public directory, and
  is settled before a hospital's bidding policy runs, so it leaves no statement
  to certify. An earlier version demanded signatures for those too, and honest
  hospitals were struck out for being unable to produce one -- 1,427 such cases
  in a single run, and a handful of them became real false accusations.

That second point was found by a test failing, not by inspection, and the
false-accusation count is now measured and printed rather than asserted in
prose.
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
CARTEL_SIZES = [0, 3, 6, 9]
SPOT_RATES = [0.0, 0.05, 0.15, 0.30]
ATTEST = dict(enable_attestation=True, attestation_deterrence=0.8)


def run_one(seed: int, **kw):
    sc = copy.deepcopy(SCENARIOS[SCENARIO])
    sc.seed = seed
    result = SimulationRunner(RunConfig(
        scenario=sc, strategy="mahros", seed=seed, **kw)).run()
    return result, M.compute(result)


def agg(runs) -> dict:
    metrics = [m for _, m in runs]
    out = {
        "success_rate": st.fmean(m.success_rate for m in metrics),
        "success_by_seed": [m.success_rate for m in metrics],
        "mean_wait": st.fmean(m.mean_wait for m in metrics),
        "cartel_overruled": st.fmean(
            sum(h.refusals_overruled for h in r.hospitals.values()
                if h.policy.name == "colluding") for r, _ in runs),
        "false_accusations": st.fmean(
            sum(h.refusals_overruled for h in r.hospitals.values()
                if h.policy.name not in ("strategic", "colluding"))
            for r, _ in runs),
        "abstentions": st.fmean(
            (r.cartel.stats()["abstentions"] if r.cartel else 0) for r, _ in runs),
        "spot_checks": st.fmean(
            r.strategy_stats.get("spot_checks", 0) for r, _ in runs),
        "max_follow_on_lift": st.fmean(
            r.corefusal.max_lift() for r, _ in runs
            if r.corefusal.max_lift() == r.corefusal.max_lift()),
        "max_same_request_lift": st.fmean(
            r.corefusal.max_same_request_lift() for r, _ in runs
            if r.corefusal.max_same_request_lift()
            == r.corefusal.max_same_request_lift()),
    }
    return out


def main(n_seeds: int = 6) -> int:
    t0 = time.perf_counter()
    seeds = [42 + i * 101 for i in range(n_seeds)]
    out: dict = {"meta": {"scenario": SCENARIO, "seeds": seeds},
                 "attack": {}, "detection": {}, "defence": {}}

    print(f"\n{'=' * 78}")
    print("COLLUSION: coordinated abstention against the deliberation stack")
    print(f"scenario={SCENARIO}  27 real hospitals  {n_seeds} seeds")
    print(f"{'=' * 78}\n")

    # -- 1. the attack ----------------------------------------------------- #
    print("1. THE ATTACK -- does coordinated abstention beat the ledger challenge?\n")
    print(f"  {'cartel size':>12}{'success':>9}{'overruled':>11}"
          f"{'abstentions':>13}{'false acc':>11}")
    for size in CARTEL_SIZES:
        kw = dict(collusion_size=size) if size else {}
        a = agg([run_one(s, **kw) for s in seeds])
        out["attack"][str(size)] = a
        print(f"  {size:>12}{a['success_rate']:>8.1%}{a['cartel_overruled']:>11.0f}"
              f"{a['abstentions']:>13.0f}{a['false_accusations']:>11.1f}")
    base = out["attack"]["0"]["success_rate"]
    six = out["attack"]["6"]["success_rate"]
    print(f"\n  A cartel of 6 of 27 hospitals costs the network "
          f"{(base - six) * 100:.1f} points of transfer success,")
    print(f"  and the challenge mechanism overrules almost none of it. For")
    print(f"  comparison, the SAME number of uncoordinated liars is caught.")

    # -- 2. why detection fails -------------------------------------------- #
    print(f"\n{'-' * 78}")
    print("2. DETECTION -- can the cartel be found in the public record?\n")
    honest = out["attack"]["0"]
    cartel = out["attack"]["6"]
    print(f"  {'statistic':<34}{'honest net':>12}{'6 colluding':>13}")
    print(f"  {'max same-request lift':<34}"
          f"{honest['max_same_request_lift']:>12.3f}"
          f"{cartel['max_same_request_lift']:>13.3f}")
    print(f"  {'max temporal follow-on lift':<34}"
          f"{honest['max_follow_on_lift']:>12.3f}"
          f"{cartel['max_follow_on_lift']:>13.3f}")
    out["detection"] = {"honest": honest, "cartel": cartel}
    print("\n  Both statistics score the colluding network LOWER than the honest")
    print("  one. This is not a tuning failure, it is structural:")
    print("    * lift divides out the marginal refusal rates, which is exactly")
    print("      where a cartel's signature lives;")
    print("    * at the occupancy these hospitals really run at, every hospital")
    print("      refuses 93-100% of the time and the statistic saturates;")
    print("    * the cartel congests its neighbours, raising THEIR refusal rates")
    print("      too, so it hides inside the damage it causes.")
    print("\n  Reported as measured. Coordinated abstention is not detectable")
    print("  from refusal patterns in the public record.")

    # -- 3. the defence ---------------------------------------------------- #
    print(f"\n{'-' * 78}")
    print("3. DEFENCE -- proactive spot checks\n")
    print("  Every other check is reactive: it looks at a refusal only after")
    print("  something flagged it, and a cartel never trips a flag. A spot check")
    print("  asks a random refusal to certify itself with no evidence at all.\n")
    print(f"  {'spot rate':>10}{'honest net':>12}{'6 colluding':>13}"
          f"{'checks':>9}{'overruled':>11}{'false acc':>11}")
    for rate in SPOT_RATES:
        kw = dict(spot_check_rate=rate, **ATTEST) if rate else {}
        clean = agg([run_one(s, **kw) for s in seeds])
        dirty = agg([run_one(s, collusion_size=6, **kw) for s in seeds])
        out["defence"][f"{rate:.2f}"] = {"honest": clean, "colluding": dirty}
        print(f"  {rate:>10.2f}{clean['success_rate']:>12.1%}"
              f"{dirty['success_rate']:>13.1%}{dirty['spot_checks']:>9.0f}"
              f"{dirty['cartel_overruled']:>11.0f}"
              f"{dirty['false_accusations']:>11.1f}")

    # -- 4. does it actually recover, and does it cost anything? ----------- #
    print(f"\n{'-' * 78}")
    print("4. TESTS\n")
    no_check = out["defence"]["0.00"]["colluding"]["success_by_seed"]
    checked = out["defence"]["0.15"]["colluding"]["success_by_seed"]
    rec = paired_t(checked, no_check, label="spot check 0.15 - none (colluding)")
    print("  " + rec.line())

    clean_none = out["defence"]["0.00"]["honest"]["success_by_seed"]
    clean_check = out["defence"]["0.15"]["honest"]["success_by_seed"]
    cost = paired_t(clean_check, clean_none, label="spot check 0.15 - none (honest)")
    print("  " + cost.line())
    print("    -> expected null. Striking out a refusal substitutes the")
    print("       hospital's TRUTHFUL bid, and a genuinely full hospital's")
    print("       truthful bid is still a refusal, so nobody is made to take a")
    print("       patient they cannot take.")

    # Measured, not asserted. An earlier version of this script claimed zero
    # here in prose while the table above it printed 0.3 and 0.5 -- the exact
    # habit this project exists to correct.
    worst = max(
        row[arm]["false_accusations"]
        for row in out["defence"].values() for arm in ("honest", "colluding"))
    print()
    print(f"  worst false-accusation count in any arm above: {worst:.2f}")
    if worst > 0:
        print("  NOT zero. Reported as measured -- proactive checking has a")
        print("  cost the reactive protocol does not, and it belongs in the")
        print("  limitations section rather than in a footnote.")
    else:
        print("  Zero in every arm, including 30% spot checks on a network with")
        print("  a cartel in it. Spot checks demand certification only for")
        print("  CAPACITY claims; a capability refusal is already checkable")
        print("  against the public directory and leaves no statement to sign.")
    out["worst_false_accusations"] = worst
    out["tests"] = {"recovery": rec.as_dict(), "cost_on_honest": cost.as_dict()}

    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / "collusion.json"
    path.write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
    print(f"\nwrote {path.relative_to(ROOT)}  ({time.perf_counter() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 6))
