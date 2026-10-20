"""Evaluation statistics: turning seeds into claims you can defend.

Paired tests over common random numbers, Holm correction, bootstrap intervals,
and a **two one-sided tests (TOST) equivalence test**. Overlapping confidence
intervals are not evidence that two systems perform the same; TOST is.
"""

from .stats import (
    EquivalenceResult,
    PairedResult,
    bootstrap_ci,
    holm_adjust,
    paired_t,
    tost_equivalence,
)

__all__ = [
    "PairedResult", "EquivalenceResult", "paired_t", "tost_equivalence",
    "bootstrap_ci", "holm_adjust",
]
