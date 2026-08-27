"""Evaluation statistics: turning seeds into claims you can defend.

`docs/MODEL_ASSUMPTIONS.md` used to end with an admission that the headline
finding -- MAHROS performs about as well as a centralised optimiser -- had no
statistical test behind it, only overlapping confidence intervals. Overlapping
intervals are not evidence of equivalence. They are the absence of evidence of
difference, which is a different and much weaker statement.

This package supplies what that claim actually needs: paired tests over common
random numbers, and a **two one-sided tests (TOST) equivalence test**, which is
the correct instrument for arguing that two systems perform the same.
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
