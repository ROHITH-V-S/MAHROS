"""MAHROS -- Multi-Agent Hospital Resource Optimization System.

Decentralised inter-hospital negotiation for post-admission escalation
transfers, with explicit fairness accounting, a tamper-evident audit ledger,
and plain-English decision explanations.
"""

__version__ = "0.1.0"

from .core.types import (  # noqa: F401
    Acuity,
    Agreement,
    Bid,
    DemographicGroup,
    Patient,
    RequestStatus,
    ResourceType,
    Specialty,
    TransferRequest,
)
