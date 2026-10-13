"""Does the Houston result hold in a different city? Phoenix, 30 real hospitals.

    python experiments/replication.py            # 5 seeds
    python experiments/replication.py 8

Every headline result was measured on one network. A reviewer will ask
whether it is a Houston artefact. Phoenix is a different network: 30
hospitals, different geography, a different capability spread, and it ran
cooler (0.65-0.88 occupied against Houston's 0.75-0.96).

The script reruns the two claims the paper rests on, unchanged:

1. coordination: MAHROS against the phone tree, the shared registry and the
   omniscient greedy optimiser, on four real Phoenix weeks;
2. verification: at 50% protective hospitals, success with refusal checking
   on and off, and false accusations.

Nothing was tuned for Phoenix; it uses the Houston code path end to end.
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
WEEKS = ["phoenix_calm", "phoenix_baseline", "phoenix_typical", "phoenix_surge"]
STRATEGIES = ["mahros", "phone", "nearest", "central"]
ADVERSARIAL_WEEK = "phoenix_surge"


def run(scenario: str, strategy: str, seed: int, **kw):
    sc = copy.deepcopy(SCENARIOS[scenario])
    sc.seed = seed
    r = SimulationRunner(RunConfig(scenario=sc, strategy=strategy, seed=seed, **kw)).run()
    return r, M.compute(r)


def main(n_seeds: int = 5) -> int:
    t0 = time.perf_counter()
    seeds = [42 + i * 101 for i in range(n_seeds)]
    out: dict = {"meta": {"seeds": seeds, "weeks": {w: SCENARIOS[w].real_week for w in WEEKS}},
                 "coordination": {}, "verification": {}}

    print(f"Phoenix replication, {n_seeds} seeds\n")
    print(f"{'week':<18}" + "".join(f"{s:>10}" for s in STRATEGIES) + f"{'MAHROS-phone':>15}")
    for week in WEEKS:
        rows = {s: [run(week, s, sd)[1].success_rate for sd in seeds] for s in STRATEGIES}
        t = paired_t(rows["mahros"], rows["phone"])
        out["coordination"][week] = {
            "success": {s: st.fmean(v) for s, v in rows.items()},
            "by_seed": rows,
            "mahros_minus_phone": {"diff": t.mean_difference, "p": t.p_value},
            "mahros_minus_nearest": paired_t(rows["mahros"], rows["nearest"]).mean_difference,
            "mahros_minus_central": paired_t(rows["mahros"], rows["central"]).mean_difference,
        }
        print(f"{week:<18}" + "".join(f"{st.fmean(rows[s]):>10.1%}" for s in STRATEGIES)
              + f"{t.mean_difference * 100:>+13.1f} pt")

    print(f"\nVerification on {ADVERSARIAL_WEEK}, 50% protective hospitals")
    arms = {}
    for label, kw in (("honest network", dict()),
                      ("50% protective, checking OFF", dict(strategic_fraction=0.5,
                                                             enable_argumentation=False)),
                      ("50% protective, checking ON", dict(strategic_fraction=0.5)),
                      ("50% protective, signed refusals", dict(
                          strategic_fraction=0.5, enable_attestation=True,
                          attestation_deterrence=0.8))):
        runs = [run(ADVERSARIAL_WEEK, "mahros", sd, **kw) for sd in seeds]
        wrong = st.fmean(sum(h.refusals_overruled for h in r.hospitals.values()
                             if h.policy.name != "strategic") for r, _ in runs)
        arms[label] = {"success": st.fmean(m.success_rate for _, m in runs),
                       "by_seed": [m.success_rate for _, m in runs],
                       "false_accusations": wrong}
        print(f"  {label:<34}{arms[label]['success']:>8.1%}   false accusations {wrong:.1f}")
    on, off = arms["50% protective, checking ON"], arms["50% protective, checking OFF"]
    honest = arms["honest network"]
    t = paired_t(on["by_seed"], off["by_seed"])
    loss = honest["success"] - off["success"]
    out["verification"] = {"arms": arms, "checking_gain": {"diff": t.mean_difference, "p": t.p_value},
                           "loss_recovered": (on["success"] - off["success"]) / loss if loss > 0 else None}
    print(f"\n  checking ON - OFF: {t.mean_difference * 100:+.1f} pts (p = {t.p_value:.4f}); "
          f"recovers {out['verification']['loss_recovered']:.0%} of the loss")

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "replication_phoenix.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote results/replication_phoenix.json  ({time.perf_counter() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 5))
