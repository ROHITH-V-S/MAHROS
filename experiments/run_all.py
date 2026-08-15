"""Full experiment sweep -> results/dashboard_data.json

Runs every strategy across every scenario for N seeds, and exports everything
the dashboard (and the paper's tables) needs in one file.

    python experiments/run_all.py            # 5 seeds, ~2 min
    python experiments/run_all.py 10         # 10 seeds
"""

from __future__ import annotations

import copy
import json
import statistics
import sys
import time
from pathlib import Path

from mahros.core.types import Acuity, RequestStatus
from mahros.ledger.interface import HashChainLedger
from mahros.sim import metrics as M
from mahros.sim.runner import RunConfig, SimulationRunner
from mahros.sim.scenario import SCENARIOS, build_network, travel_time_matrix

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"

SCENARIO_ORDER = ["baseline", "surge", "scarcity", "surge_scarcity"]
STRATEGY_ORDER = ["mahros", "phone", "central", "nearest", "none"]
STRATEGY_LABELS = {
    "mahros": "MAHROS",
    "phone": "Phone tree (today)",
    "central": "Centralized (omniscient)",
    "nearest": "Nearest available",
    "none": "No transfers",
}


def run_one(scenario: str, strategy: str, seed: int, **kw):
    sc = copy.deepcopy(SCENARIOS[scenario])
    sc.seed = seed
    return SimulationRunner(RunConfig(scenario=sc, strategy=strategy, seed=seed, **kw)).run()


def main(n_seeds: int = 5) -> None:
    t_start = time.perf_counter()
    seeds = [42 + i * 101 for i in range(n_seeds)]
    out: dict = {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "n_seeds": n_seeds,
            "seeds": seeds,
            "scenarios": SCENARIO_ORDER,
            "strategies": STRATEGY_ORDER,
            "strategy_labels": STRATEGY_LABELS,
        },
        "results": {},
        "deltas": {},
    }

    for scenario in SCENARIO_ORDER:
        out["results"][scenario] = {}
        for strategy in STRATEGY_ORDER:
            runs = [M.compute(run_one(scenario, strategy, s)) for s in seeds]
            agg = M.aggregate(runs)
            # extra fields the dashboard wants that aggregate() does not carry
            agg["decision_concentration"] = round(
                statistics.fmean(r.decision_concentration for r in runs), 3)
            agg["disclosure_concentration"] = round(
                statistics.fmean(r.disclosure_concentration for r in runs), 3)
            agg["decentralised"] = all(r.decentralised for r in runs)
            agg["ledger_valid"] = all(r.ledger_valid for r in runs)
            agg["privacy_violations"] = sum(r.privacy_violations for r in runs)
            agg["n_requests_mean"] = round(
                statistics.fmean(r.n_requests for r in runs), 1)
            agg["label"] = STRATEGY_LABELS[strategy]
            out["results"][scenario][strategy] = agg
            print(f"  {scenario:16s} {strategy:8s} "
                  f"success={agg['success_rate']:.1%} "
                  f"wait={agg['mean_wait']:.1f}min "
                  f"gini={agg['burden_gini']:.3f}")

        base = out["results"][scenario]
        out["deltas"][scenario] = {
            "vs_phone": M.compare(base["phone"], base["mahros"]),
            "vs_central": M.compare(base["central"], base["mahros"]),
        }

    # -- ablation ---------------------------------------------------------- #
    print("\nablation on surge_scarcity...")
    ablation_arms = {
        "full": {},
        "no_fairness": {"fairness_enabled": False},
        "no_audit": {"audit_enabled": False},
    }
    out["ablation"] = {}
    for label, kw in ablation_arms.items():
        runs = [M.compute(run_one("surge_scarcity", "mahros", s, **kw)) for s in seeds]
        out["ablation"][label] = M.aggregate(runs)
        print(f"  {label:14s} gini={out['ablation'][label]['burden_gini']:.3f} "
              f"wait={out['ablation'][label]['mean_wait']:.1f}min")

    # -- per-hospital detail from one representative run -------------------- #
    print("\nper-hospital detail + sample decisions...")
    detail = run_one("surge_scarcity", "mahros", seeds[0])
    detail_nofair = run_one("surge_scarcity", "mahros", seeds[0], fairness_enabled=False)

    def hospital_rows(res):
        rows = []
        for hid, h in sorted(res.hospitals.items()):
            crit = sum(n for r, n in h.cfg.capacities.items() if r.is_critical)
            rows.append({
                "id": hid, "tier": h.tier, "name": h.name,
                "x": round(h.cfg.x, 2), "y": round(h.cfg.y, 2),
                "critical_capacity": crit,
                "specialties": sorted(s.value for s in h.specialties),
                "accepted": h.accepted_count,
                "sent": h.sent_count,
                "strain": round(h.resources.overall_strain(), 3),
            })
        return rows

    out["hospitals"] = hospital_rows(detail)
    out["hospitals_no_fairness"] = hospital_rows(detail_nofair)

    # travel matrix for the network map
    cfgs = [h.cfg for h in detail.hospitals.values()]
    tt = travel_time_matrix(cfgs, detail.config.scenario.ambulance_speed_kmh)
    out["travel_minutes"] = {f"{a}|{b}": round(v, 1) for (a, b), v in tt.items() if a != b}

    # -- sample negotiated decisions with rationales ------------------------ #
    samples = []
    for a in detail.agreements[:400]:
        if not a.rationale:
            continue
        req = next((r for r in detail.requests if r.request_id == a.request_id), None)
        if req is None:
            continue
        samples.append({
            "origin": a.origin, "receiver": a.receiver,
            "resource": a.resource.value,
            "specialty": req.specialty.value,
            "acuity": int(req.acuity),
            "acuity_name": req.acuity.name,
            "window_min": req.acuity.safe_window_minutes,
            "wait_min": round(req.wait_minutes, 1) if req.wait_minutes else None,
            "peers_contacted": req.n_peers_contacted,
            "bids_received": req.n_bids_received,
            "decided_by": a.decided_by,
            "rationale": a.rationale,
            "terms_hash": a.terms_hash[:32],
            "agreed_at_min": round(a.agreed_at, 1),
        })
        if len(samples) >= 12:
            break
    out["sample_decisions"] = samples

    # -- request-level distribution for the wait-time histogram ------------- #
    out["wait_distribution"] = {}
    for strategy in ["mahros", "phone", "central"]:
        res = run_one("surge_scarcity", strategy, seeds[0])
        waits = [round(r.wait_minutes, 1) for r in res.requests
                 if r.status is RequestStatus.COMPLETED and r.wait_minutes is not None]
        out["wait_distribution"][strategy] = waits

    # -- acuity breakdown --------------------------------------------------- #
    out["by_acuity"] = {}
    for strategy in ["mahros", "phone", "central"]:
        res = run_one("surge_scarcity", strategy, seeds[0])
        rows = {}
        for lvl in [2, 3, 4, 5]:
            grp = [r for r in res.requests if int(r.acuity) == lvl]
            if not grp:
                continue
            done = [r for r in grp if r.status is RequestStatus.COMPLETED]
            rows[str(lvl)] = {
                "n": len(grp),
                "success": round(len(done) / len(grp), 4),
                "mean_wait": round(
                    statistics.fmean([r.wait_minutes for r in done]), 1) if done else 0.0,
                "window": Acuity(lvl).safe_window_minutes,
            }
        out["by_acuity"][strategy] = rows

    # -- ledger tamper demonstration ---------------------------------------- #
    led = detail.ledger
    before = led.verify()
    forged = None
    for block in led.chain[1:]:
        if block.entries:
            forged = {"block": block.index,
                      "field": "receiver",
                      "was": block.entries[0]["receiver"],
                      "now": "H99_FORGED"}
            block.entries[0]["receiver"] = "H99_FORGED"
            break
    after = led.verify()
    out["ledger_demo"] = {
        "before": {k: before[k] for k in ("valid", "blocks", "records")},
        "tamper": forged,
        "after": {"valid": after["valid"], "errors": after["errors"][:3]},
    }

    # -- privacy demonstration ---------------------------------------------- #
    from mahros.privacy.anonymizer import TokenizingAnonymizer
    anon = TokenizingAnonymizer()
    raw = ("Patient Mr. Rahul Sharma, MRN H03-000891, contact +91 9812345678, "
           "admitted 14/03/2026, email rahul.sharma@example.com. "
           "Deteriorating, needs neuro ICU.")
    out["privacy_demo"] = {
        "raw_note": raw,
        "scrubbed_note": anon.scrub(raw),
        "example_public_view": detail.requests[0].public_view() if detail.requests else {},
    }

    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / "dashboard_data.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {path}  ({path.stat().st_size / 1024:.0f} KB) "
          f"in {time.perf_counter() - t_start:.1f}s")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 5)
