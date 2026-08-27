"""Evaluation harness: turns a RunResult into the numbers that go in the paper.

Metric families, mapped to the claims they support:

    clinical     -> "faster to definitive care"      (wait, p90, breach rate)
    operational  -> "works at scale during surge"    (success rate, staff minutes)
    efficiency   -> "does not waste capacity"        (utilisation, load spread)
    fairness     -> "does not dump on one hospital"  (Gini, Jain, equity gap)
    trust        -> "auditable and private"          (ledger integrity, leakage)
    cost         -> "cheap enough to be real"        (messages, LLM calls)
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..core.types import Acuity, RequestStatus, TransferRequest
from ..fairness.metrics import equity_report, gini, jain_index
from ..privacy.leakage import measure_leakage
from .runner import RunResult


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return xs[int(k)]
    return xs[lo] * (hi - k) + xs[hi] * (k - lo)


@dataclass
class Metrics:
    strategy: str = ""
    scenario: str = ""
    seed: int = 0

    # clinical
    n_requests: int = 0
    n_completed: int = 0
    success_rate: float = 0.0
    mean_wait: float = 0.0
    median_wait: float = 0.0
    p90_wait: float = 0.0
    breach_rate: float = 0.0
    critical_breach_rate: float = 0.0
    mean_wait_critical: float = 0.0

    # operational
    mean_peers_contacted: float = 0.0
    total_coordinator_hours: float = 0.0
    coordinator_minutes_per_transfer: float = 0.0

    # efficiency
    mean_icu_utilisation: float = 0.0
    mean_critical_utilisation: float = 0.0
    rejected_admission_rate: float = 0.0

    # fairness
    burden_gini: float = 0.0
    burden_jain: float = 0.0
    burden_max_min_gap: float = 0.0
    wait_equity_gap: float = 0.0
    breach_equity_gap: float = 0.0

    # trust
    ledger_valid: bool = False
    ledger_records: int = 0
    privacy_violations: int = 0
    decentralised: bool = False
    max_peers_disclosed: int = 0
    decision_concentration: float = 0.0
    disclosure_concentration: float = 0.0
    # How much a hospital's private occupancy is actually revealed by what it
    # says, in bits. `disclosure_concentration` only measures who *receives*
    # capacity messages, which is a topology property; these measure what those
    # messages give away. See mahros/privacy/leakage.py.
    leaked_bits_per_transfer: float = 0.0
    leaked_bits_per_message: float = 0.0
    leakage_fraction_of_maximum: float = 0.0

    # cost
    messages: int = 0
    messages_per_transfer: float = 0.0
    kb_exchanged: float = 0.0
    llm_calls: int = 0

    extra: dict[str, Any] = field(default_factory=dict)

    def row(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "extra"}
        d.update({f"x_{k}": v for k, v in self.extra.items()})
        return d


def compute(result: RunResult) -> Metrics:
    reqs = result.requests
    cfg = result.config
    m = Metrics(
        strategy=cfg.strategy,
        scenario=cfg.scenario.name,
        seed=cfg.seed,
        n_requests=len(reqs),
    )
    if not reqs:
        return m

    completed = [r for r in reqs if r.status is RequestStatus.COMPLETED]
    waits = [r.wait_minutes for r in completed if r.wait_minutes is not None]
    critical = [r for r in reqs if r.acuity >= Acuity.CRITICAL]
    critical_waits = [
        r.wait_minutes for r in critical
        if r.status is RequestStatus.COMPLETED and r.wait_minutes is not None
    ]

    m.n_completed = len(completed)
    m.success_rate = len(completed) / len(reqs)
    m.mean_wait = statistics.fmean(waits) if waits else 0.0
    m.median_wait = statistics.median(waits) if waits else 0.0
    m.p90_wait = _pct(waits, 0.90)
    m.breach_rate = sum(1 for r in reqs if r.breached) / len(reqs)
    m.critical_breach_rate = (
        sum(1 for r in critical if r.breached) / len(critical) if critical else 0.0
    )
    m.mean_wait_critical = statistics.fmean(critical_waits) if critical_waits else 0.0

    m.mean_peers_contacted = statistics.fmean([r.n_peers_contacted for r in reqs])
    coord_minutes = sum(h.coordinator_busy_minutes for h in result.hospitals.values())
    coord_minutes += sum(r.coordination_minutes for r in reqs)
    m.total_coordinator_hours = coord_minutes / 60.0
    m.coordinator_minutes_per_transfer = coord_minutes / len(reqs)

    # utilisation
    icu_vals, crit_vals = [], []
    for h in result.hospitals.values():
        util = h.resources.mean_utilisation()
        for res, u in util.items():
            if res.value == "icu_bed":
                icu_vals.append(u)
            if res.is_critical:
                crit_vals.append(u)
    m.mean_icu_utilisation = statistics.fmean(icu_vals) if icu_vals else 0.0
    m.mean_critical_utilisation = statistics.fmean(crit_vals) if crit_vals else 0.0
    m.rejected_admission_rate = (
        result.rejected_admissions / result.total_admissions
        if result.total_admissions else 0.0
    )

    # fairness
    if result.fairness is not None:
        fs = result.fairness.summary()
        m.burden_gini = fs["burden_gini"]
        m.burden_jain = fs["burden_jain"]
        m.burden_max_min_gap = fs["burden_max_min_gap"]
    eq = equity_report(reqs)
    m.wait_equity_gap = eq.wait_equity_gap
    m.breach_equity_gap = eq.breach_equity_gap
    m.extra["equity"] = eq.as_dict()

    # trust
    if result.ledger is not None:
        v = result.ledger.verify()
        m.ledger_valid = bool(v["valid"])
        m.ledger_records = int(v["records"])
    if result.privacy is not None:
        pr = result.privacy.report()
        m.privacy_violations = pr["violations"]
        m.extra["privacy"] = pr
    if result.bus is not None:
        audit = result.bus.audit_no_global_view(set(result.hospitals))
        m.decentralised = bool(audit["is_decentralised"])
        m.max_peers_disclosed = audit["max_peers_disclosed_to_one_node"]
        m.decision_concentration = audit["decision_concentration"]
        m.disclosure_concentration = audit["disclosure_concentration"]
        m.extra["decentralisation"] = audit
        m.messages = result.bus.message_count
        m.messages_per_transfer = result.bus.message_count / len(reqs)
        m.kb_exchanged = result.bus.bytes_exchanged / 1024.0

        # What the messages actually give away, as opposed to who receives them.
        leak = measure_leakage(result.bus, result.hospitals)
        m.leaked_bits_per_transfer = leak.total_bits / len(reqs)
        m.leaked_bits_per_message = leak.mean_bits_per_message
        m.leakage_fraction_of_maximum = leak.disclosure_fraction
        m.extra["leakage"] = leak.as_dict()

    if result.coordinator is not None:
        cs = result.coordinator.stats()
        m.llm_calls = cs.get("live_calls", 0)
        m.extra["llm"] = cs

    m.extra["strategy_stats"] = result.strategy_stats
    m.extra["wall_seconds"] = round(result.wall_seconds, 2)
    return m


def cfg_requires_central(cfg) -> bool:
    from .strategies import STRATEGIES
    return STRATEGIES[cfg.strategy].requires_central_data


# --------------------------------------------------------------------------- #
# Aggregation across seeds -- what actually goes in a results table
# --------------------------------------------------------------------------- #

_HEADLINE = [
    "success_rate", "mean_wait", "p90_wait", "breach_rate",
    "critical_breach_rate", "mean_wait_critical", "burden_gini",
    "burden_jain", "wait_equity_gap", "mean_icu_utilisation",
    "coordinator_minutes_per_transfer", "messages_per_transfer",
]


def aggregate(runs: Iterable[Metrics]) -> dict[str, Any]:
    """Mean +/- 95% CI across seeds. Report this, never a single seed."""
    runs = list(runs)
    if not runs:
        return {}
    out: dict[str, Any] = {
        "strategy": runs[0].strategy,
        "scenario": runs[0].scenario,
        "n_seeds": len(runs),
        "n_requests_total": sum(r.n_requests for r in runs),
    }
    for field_name in _HEADLINE:
        vals = [getattr(r, field_name) for r in runs]
        mean = statistics.fmean(vals)
        if len(vals) > 1:
            sd = statistics.stdev(vals)
            ci = 1.96 * sd / math.sqrt(len(vals))
        else:
            sd = ci = 0.0
        out[field_name] = round(mean, 4)
        out[f"{field_name}_ci95"] = round(ci, 4)
        out[f"{field_name}_sd"] = round(sd, 4)
    return out


def compare(baseline: dict[str, Any], treatment: dict[str, Any]) -> dict[str, Any]:
    """Relative improvement of `treatment` over `baseline` on headline metrics."""
    #: metrics where lower is better
    lower_better = {
        "mean_wait", "p90_wait", "breach_rate", "critical_breach_rate",
        "mean_wait_critical", "burden_gini", "wait_equity_gap",
        "coordinator_minutes_per_transfer",
    }
    out: dict[str, Any] = {}
    for k in _HEADLINE:
        b, t = baseline.get(k), treatment.get(k)
        if b in (None, 0) or t is None:
            continue
        delta = (t - b) / abs(b) * 100.0
        out[k] = {
            "baseline": b,
            "treatment": t,
            "pct_change": round(delta, 2),
            "better": (delta < 0) if k in lower_better else (delta > 0),
        }
    return out
