"""Why not just a shared bed registry? Because it believes whatever it is told.

    python experiments/registry_attack.py          # 6 seeds

On an honest network a shared registry (nearest hospital with declared room)
matches MAHROS. That is the strongest objection to the project. A registry
has no way to check a declaration, so this measures both under the same
protective hospitals (they report "full" for patients they do not want).

Both arms see the same hospitals, the same patients and the same lies.
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
SCENARIOS_RUN = ["houston_surge", "phoenix_surge"]
FRACTIONS = [0.0, 0.25, 0.5]


def success(scenario: str, strategy: str, seed: int, **kw) -> float:
    sc = copy.deepcopy(SCENARIOS[scenario])
    sc.seed = seed
    return M.compute(SimulationRunner(RunConfig(
        scenario=sc, strategy=strategy, seed=seed, **kw)).run()).success_rate


def main(n_seeds: int = 6) -> int:
    t0 = time.perf_counter()
    seeds = [42 + i * 101 for i in range(n_seeds)]
    out: dict = {"meta": {"seeds": seeds}, "scenarios": {}}
    for scenario in SCENARIOS_RUN:
        print(f"\n{scenario}: success with protective hospitals, {n_seeds} seeds")
        print(f"{'protective':>11}{'registry':>10}{'MAHROS':>9}{'MAHROS-registry':>17}")
        rows = {}
        for frac in FRACTIONS:
            reg = [success(scenario, "nearest", s, strategic_fraction=frac) for s in seeds]
            mah = [success(scenario, "mahros", s, strategic_fraction=frac) for s in seeds]
            t = paired_t(mah, reg)
            rows[f"{frac:.2f}"] = {"registry": st.fmean(reg), "mahros": st.fmean(mah),
                                   "diff": t.mean_difference, "p": t.p_value}
            print(f"{frac:>11.0%}{st.fmean(reg):>10.1%}{st.fmean(mah):>9.1%}"
                  f"{t.mean_difference * 100:>+14.1f} pt  (p = {t.p_value:.4f})")
        out["scenarios"][scenario] = rows
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "registry_attack.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote results/registry_attack.json  ({time.perf_counter() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 6))
