"""Diagnostic: why do transfer requests fail?

Run this whenever a success rate looks wrong. A simulation that silently fails
most transfers will produce a beautiful, meaningless results table.
"""

from __future__ import annotations

import collections
import copy
import sys

from mahros.core.types import Acuity, RequestStatus
from mahros.sim.runner import RunConfig, SimulationRunner
from mahros.sim.scenario import SCENARIOS


def main(scenario: str = "baseline", strategy: str = "mahros", seed: int | str = 42) -> None:
    seed = int(seed)
    sc = copy.deepcopy(SCENARIOS[scenario])
    sc.seed = seed
    result = SimulationRunner(RunConfig(scenario=sc, strategy=strategy, seed=seed)).run()

    reqs = result.requests
    print(f"\n=== {strategy} on {scenario} (seed {seed}) ===")
    print(f"admissions         : {result.total_admissions}")
    print(f"rejected admissions: {result.rejected_admissions} "
          f"({result.rejected_admissions / max(1, result.total_admissions):.1%})")
    print(f"transfer requests  : {len(reqs)}")

    by_status = collections.Counter(r.status.value for r in reqs)
    print("\nstatus:")
    for k, v in by_status.most_common():
        print(f"  {k:12s} {v:5d}  ({v / max(1, len(reqs)):.1%})")

    print("\nfailure reasons:")
    for k, v in collections.Counter(
        r.failure_reason for r in reqs if r.status is not RequestStatus.COMPLETED
    ).most_common():
        print(f"  {str(k):32s} {v:5d}")

    print("\nby resource (success rate):")
    per_res: dict[str, list[int]] = collections.defaultdict(list)
    for r in reqs:
        per_res[r.resource.value].append(1 if r.status is RequestStatus.COMPLETED else 0)
    for k, v in sorted(per_res.items()):
        print(f"  {k:12s} n={len(v):4d}  success={sum(v) / len(v):.1%}")

    print("\nby acuity (success rate, mean wait):")
    per_ac: dict[int, list] = collections.defaultdict(list)
    for r in reqs:
        per_ac[int(r.acuity)].append(r)
    for k in sorted(per_ac):
        group = per_ac[k]
        done = [g for g in group if g.status is RequestStatus.COMPLETED]
        mw = sum(g.wait_minutes for g in done) / len(done) if done else 0.0
        print(f"  acuity {k} (window {Acuity(k).safe_window_minutes:4d} min) "
              f"n={len(group):4d}  success={len(done) / len(group):.1%}  "
              f"mean wait={mw:6.1f} min")

    print("\nbid-level refusal reasons (all peers, all rounds):")
    # re-derive from the message transcript
    from mahros.negotiation.messages import Perf
    reasons = collections.Counter(
        m.content.get("reason", "?") for m in result.bus.transcript
        if m.performative is Perf.REFUSE
    )
    total_refusals = sum(reasons.values())
    for k, v in reasons.most_common():
        print(f"  {str(k):32s} {v:6d}  ({v / max(1, total_refusals):.1%})")

    print("\nnetwork strain at end of run:")
    for hid, h in sorted(result.hospitals.items()):
        print(f"  {hid} tier{h.tier} strain={h.resources.overall_strain():.2f} "
              f"accepted={h.accepted_count:3d} sent={h.sent_count:3d} "
              f"refused={h.refused_count:5d}")

    print("\ntravel-time sanity (min / median / max minutes between hospitals):")
    tts = [v for (a, b), v in result_tt(result).items() if a != b]
    tts.sort()
    print(f"  {tts[0]:.1f} / {tts[len(tts) // 2]:.1f} / {tts[-1]:.1f}")


def result_tt(result) -> dict:
    from mahros.sim.scenario import build_network, travel_time_matrix
    cfgs = [h.cfg for h in result.hospitals.values()]
    return travel_time_matrix(cfgs, result.config.scenario.ambulance_speed_kmh)


if __name__ == "__main__":
    main(*(sys.argv[1:] or []))
