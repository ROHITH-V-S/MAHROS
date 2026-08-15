"""Layer 4: fairness measurement.

Two distinct fairness questions, deliberately kept separate because reviewers
will ask which one you mean:

  1. **Burden fairness (provider-side).** Is the network repeatedly dumping
     transfers on the same hospital? Measured on the net-burden vector with
     Gini, Jain's index, and max-min gap.

  2. **Outcome fairness (patient-side).** Do some patient groups systematically
     wait longer or get refused more? Measured as the *equity gap*: the
     difference in mean wait / breach rate between the best- and worst-served
     stratum.

`group` is used for measurement only and is never exposed to bidders -- see
`TransferRequest.public_view`.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable

from ..core.types import DemographicGroup, TransferRequest


# --------------------------------------------------------------------------- #
# Inequality indices
# --------------------------------------------------------------------------- #

def gini(values: Iterable[float]) -> float:
    """Gini coefficient. 0 = perfectly equal, ->1 = maximally unequal.

    Shifted to handle negative net-burden values (a hospital can be a net
    sender). Returns 0.0 for degenerate input.
    """
    xs = sorted(values)
    n = len(xs)
    if n == 0:
        return 0.0
    lo = min(xs)
    if lo < 0:
        xs = [x - lo for x in xs]
    total = sum(xs)
    if total <= 0:
        return 0.0
    cum = sum((i + 1) * x for i, x in enumerate(xs))
    return (2.0 * cum) / (n * total) - (n + 1.0) / n


def jain_index(values: Iterable[float]) -> float:
    """Jain's fairness index in (0, 1]. 1 = perfectly fair."""
    xs = [max(0.0, v) for v in values]
    n = len(xs)
    if n == 0:
        return 1.0
    s = sum(xs)
    sq = sum(x * x for x in xs)
    if sq == 0:
        return 1.0
    return (s * s) / (n * sq)


def max_min_gap(values: Iterable[float]) -> float:
    xs = list(values)
    return (max(xs) - min(xs)) if xs else 0.0


# --------------------------------------------------------------------------- #
# Live fairness state used *during* negotiation
# --------------------------------------------------------------------------- #

@dataclass
class BurdenRecord:
    accepted: int = 0
    sent: int = 0
    accepted_los_minutes: float = 0.0

    @property
    def net(self) -> int:
        return self.accepted - self.sent


class FairnessLedger:
    """Rolling record of who has been carrying the load.

    Exposes `credit(hospital)`: positive means the hospital has taken more than
    its share recently and should be *shielded*; negative means it owes the
    network. The award scorer turns this into a bounded tie-break bonus -- it
    never overrides clinical feasibility or the safe-window constraint.
    """

    def __init__(self, hospitals: Iterable[str], window: int = 200,
                 capacity_weights: dict[str, float] | None = None) -> None:
        self.hospitals = list(hospitals)
        self.records: dict[str, BurdenRecord] = {h: BurdenRecord() for h in self.hospitals}
        self.recent: deque[str] = deque(maxlen=window)
        # Fair share is proportional to capacity, not uniform: a 200-bed
        # tertiary centre should absorb more than a 20-bed district hospital.
        self.weights = capacity_weights or {h: 1.0 for h in self.hospitals}
        total = sum(self.weights.values()) or 1.0
        self.share = {h: w / total for h, w in self.weights.items()}

    def record_accept(self, hospital: str, los_minutes: float = 0.0) -> None:
        rec = self.records.setdefault(hospital, BurdenRecord())
        rec.accepted += 1
        rec.accepted_los_minutes += los_minutes
        self.recent.append(hospital)

    def record_send(self, hospital: str) -> None:
        self.records.setdefault(hospital, BurdenRecord()).sent += 1

    def recent_load(self, hospital: str) -> int:
        return sum(1 for h in self.recent if h == hospital)

    def credit(self, hospital: str) -> float:
        """Deviation from fair share over the rolling window, in [-1, 1].

        > 0  : over-burdened relative to capacity share  -> deprioritise
        < 0  : under-contributing                        -> prioritise
        """
        n = len(self.recent)
        if n == 0:
            return 0.0
        actual = self.recent_load(hospital) / n
        expected = self.share.get(hospital, 1.0 / max(1, len(self.hospitals)))
        return max(-1.0, min(1.0, (actual - expected) / max(expected, 1e-6)))

    # -- reporting --------------------------------------------------------- #
    def burden_vector(self) -> list[float]:
        return [self.records[h].accepted for h in self.hospitals]

    def normalised_burden_vector(self) -> list[float]:
        """Accepted transfers per unit of capacity share -- the fair comparison."""
        return [
            self.records[h].accepted / max(self.share.get(h, 1e-6), 1e-6)
            for h in self.hospitals
        ]

    def summary(self) -> dict[str, float]:
        raw = self.burden_vector()
        norm = self.normalised_burden_vector()
        return {
            "burden_gini": gini(norm),
            "burden_jain": jain_index(norm),
            "burden_max_min_gap": max_min_gap(raw),
            "total_accepted": float(sum(raw)),
        }


# --------------------------------------------------------------------------- #
# Patient-side equity, computed post-hoc over completed requests
# --------------------------------------------------------------------------- #

@dataclass
class EquityReport:
    by_group_wait: dict[str, float] = field(default_factory=dict)
    by_group_breach: dict[str, float] = field(default_factory=dict)
    by_group_n: dict[str, int] = field(default_factory=dict)
    wait_equity_gap: float = 0.0
    breach_equity_gap: float = 0.0
    wait_gini: float = 0.0

    def as_dict(self) -> dict:
        return {
            "wait_equity_gap_min": round(self.wait_equity_gap, 2),
            "breach_equity_gap": round(self.breach_equity_gap, 4),
            "wait_gini": round(self.wait_gini, 4),
            "by_group_wait_min": {k: round(v, 1) for k, v in self.by_group_wait.items()},
            "by_group_breach_rate": {k: round(v, 3) for k, v in self.by_group_breach.items()},
            "by_group_n": self.by_group_n,
        }


def equity_report(requests: Iterable[TransferRequest]) -> EquityReport:
    waits: dict[str, list[float]] = defaultdict(list)
    breaches: dict[str, list[int]] = defaultdict(list)

    for r in requests:
        key = r.group.value if isinstance(r.group, DemographicGroup) else str(r.group)
        breaches[key].append(1 if r.breached else 0)
        w = r.wait_minutes
        if w is not None:
            waits[key].append(w)

    rep = EquityReport()
    for g, vals in waits.items():
        rep.by_group_wait[g] = sum(vals) / len(vals) if vals else 0.0
    for g, vals in breaches.items():
        rep.by_group_breach[g] = sum(vals) / len(vals) if vals else 0.0
        rep.by_group_n[g] = len(vals)

    if rep.by_group_wait:
        rep.wait_equity_gap = max_min_gap(rep.by_group_wait.values())
        rep.wait_gini = gini(rep.by_group_wait.values())
    if rep.by_group_breach:
        rep.breach_equity_gap = max_min_gap(rep.by_group_breach.values())
    return rep
