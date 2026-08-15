"""Resource pools with explicit *reservation* semantics.

Reservation matters: in a decentralised protocol a hospital may be bidding on
several requests at once. Without reserving at award time you get double
allocation, which silently inflates your results. This is the single most
common bug in naive Contract Net implementations.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.types import ResourceType


@dataclass
class ResourcePool:
    resource: ResourceType
    capacity: int
    occupied: int = 0
    reserved: int = 0                    # awarded but patient not yet arrived
    _reservations: dict[str, float] = field(default_factory=dict)  # request_id -> expiry

    @property
    def available(self) -> int:
        return self.capacity - self.occupied - self.reserved

    @property
    def strain(self) -> float:
        """Occupancy including reservations, 0..1+. Drives bid pricing."""
        if self.capacity == 0:
            return 1.0
        return (self.occupied + self.reserved) / self.capacity

    def can_reserve(self) -> bool:
        return self.available > 0

    def reserve(self, request_id: str, hold_until: float) -> bool:
        if not self.can_reserve():
            return False
        self.reserved += 1
        self._reservations[request_id] = hold_until
        return True

    def release_reservation(self, request_id: str) -> None:
        """Called when a transfer is cancelled or the hold lapses."""
        if self._reservations.pop(request_id, None) is not None:
            self.reserved = max(0, self.reserved - 1)

    def occupy(self, request_id: str | None = None) -> bool:
        """Convert a reservation into an occupancy (patient arrived)."""
        if request_id is not None and request_id in self._reservations:
            self._reservations.pop(request_id)
            self.reserved = max(0, self.reserved - 1)
            self.occupied += 1
            return True
        if self.available <= 0:
            return False
        self.occupied += 1
        return True

    def free(self) -> None:
        self.occupied = max(0, self.occupied - 1)

    def expire_holds(self, now: float) -> list[str]:
        """Drop reservations whose hold window lapsed. Returns freed request ids."""
        stale = [rid for rid, exp in self._reservations.items() if exp <= now]
        for rid in stale:
            self.release_reservation(rid)
        return stale


class ResourceLedger:
    """All pools for one hospital, plus utilisation sampling for evaluation."""

    def __init__(self, capacities: dict[ResourceType, int]) -> None:
        self.pools: dict[ResourceType, ResourcePool] = {
            r: ResourcePool(resource=r, capacity=c) for r, c in capacities.items()
        }
        self._util_samples: dict[ResourceType, list[float]] = {r: [] for r in capacities}

    def pool(self, resource: ResourceType) -> ResourcePool | None:
        return self.pools.get(resource)

    def available(self, resource: ResourceType) -> int:
        p = self.pools.get(resource)
        return p.available if p else 0

    def strain(self, resource: ResourceType) -> float:
        p = self.pools.get(resource)
        return p.strain if p else 1.0

    def overall_strain(self) -> float:
        """Capacity-weighted mean strain across critical resources."""
        crit = [(p.capacity, p.strain) for r, p in self.pools.items() if r.is_critical]
        total = sum(c for c, _ in crit)
        if total == 0:
            return 0.0
        return sum(c * s for c, s in crit) / total

    def sample_utilisation(self) -> None:
        for r, p in self.pools.items():
            self._util_samples[r].append(p.occupied / p.capacity if p.capacity else 0.0)

    def mean_utilisation(self) -> dict[ResourceType, float]:
        return {
            r: (sum(v) / len(v) if v else 0.0) for r, v in self._util_samples.items()
        }
