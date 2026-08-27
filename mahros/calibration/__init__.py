"""External calibration: checking the model against real hospital data.

The single most common objection to a simulation paper is "your numbers are
invented." This package answers it by comparing the simulation's structural
assumptions against a **public, facility-level, real-world dataset** of 3,186
US short-term acute-care hospitals, and reporting where the model agrees and
where it does not.

We do not claim the simulated network *is* that dataset -- MAHROS models a
district referral cluster, not the US hospital system. The claim is narrower
and defensible: the *structural ratios* the results depend on (how full
hospitals run, how many ICU beds a hospital has per inpatient bed, how many
small hospitals cannot escalate at all) are consistent with observed reality.

See `mahros.calibration.hhs` and `python -m mahros.cli calibrate`.
"""

from .hhs import CalibrationReport, load_reference, validate_scenario

__all__ = ["CalibrationReport", "load_reference", "validate_scenario"]
