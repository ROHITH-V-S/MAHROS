"""How much does a hospital actually give away when it answers?

The problem with the old measure
--------------------------------
`MessageBus.audit_no_global_view` counts what *share* of capacity disclosures
any single node receives. MAHROS scores ~15%, a central optimiser scores 100%,
and that looks like a result. It is not much of one. The number is essentially
1/n plus demand skew: it falls out of the fact that each hospital brokers its
own patients' transfers, which is a topology choice, not a measured property.
The giveaway is that the phone-tree baseline scores the same ~15%. A metric that
cannot distinguish MAHROS from a telephone is measuring the wrong thing.

What this module measures instead
---------------------------------
**How many bits about a hospital's private occupancy each message it sends
actually carries.** Not how many messages, not who they went to -- how much a
recipient learns from them.

The model is deliberately simple and stated in full, because a leakage number
nobody can check is worthless:

  * Before hearing anything, an observer's belief about hospital *j*'s occupancy
    of a resource is uniform over the `capacity + 1` possible levels. That is
    `log2(capacity + 1)` bits of uncertainty.
  * Each message rules some levels out. What survives is the *consistent set*.
  * The information disclosed is the reduction in entropy:
    `log2(|before|) - log2(|after|)` bits.

Worked through, this is what the three answers cost:

  ``PROPOSE`` without strain (MAHROS)
      "I can take them." Rules out only the levels at or above the protection
      reserve. On a 26-bed ICU that is about 0.06 bits -- the observer learns
      that you are not nearly full, and nothing else.

  ``REFUSE`` with ``at_self_protection_reserve`` (MAHROS)
      "I'm down to my last one." Much more revealing: it pins occupancy to the
      top two or three levels, roughly 3.2 bits on the same unit. Refusing is
      the *expensive* answer, privacy-wise -- which is a genuinely interesting
      property to report, and the opposite of what people assume.

  ``PROPOSE`` carrying explicit strain (centralised arms)
      Pins occupancy exactly: the full `log2(capacity + 1)` bits, every time.

So the difference between MAHROS and a central optimiser is not that fewer
messages are sent. It is that MAHROS's messages **say less**, and this module
puts a number on how much less.

Caveat, stated plainly: this measures single-message disclosure against a
uniform prior. It does not model an adversary that accumulates observations over
time and reconstructs an occupancy *trajectory*, which would leak strictly more.
The numbers here are therefore a lower bound on total leakage, and the
comparison between arms is the claim, not the absolute value.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..negotiation.messages import MessageBus, Perf


@dataclass
class LeakageReport:
    total_messages: int = 0
    informative_messages: int = 0
    total_bits: float = 0.0
    bits_by_performative: dict[str, float] = field(default_factory=dict)
    bits_per_hospital: dict[str, float] = field(default_factory=dict)
    #: hospital -> observer -> bits that observer learned about it
    worst_pair: tuple[str, str, float] = ("", "", 0.0)
    max_possible_bits: float = 0.0

    @property
    def mean_bits_per_message(self) -> float:
        if not self.informative_messages:
            return 0.0
        return self.total_bits / self.informative_messages

    @property
    def disclosure_fraction(self) -> float:
        """Share of the maximum leakage a fully transparent system would give."""
        if self.max_possible_bits <= 0:
            return 0.0
        return self.total_bits / self.max_possible_bits

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_messages": self.total_messages,
            "informative_messages": self.informative_messages,
            "total_bits_disclosed": round(self.total_bits, 2),
            "mean_bits_per_message": round(self.mean_bits_per_message, 4),
            "disclosure_fraction_of_maximum": round(self.disclosure_fraction, 4),
            "bits_by_performative": {k: round(v, 2)
                                     for k, v in self.bits_by_performative.items()},
            "worst_observer_pair": {
                "target": self.worst_pair[0],
                "observer": self.worst_pair[1],
                "bits": round(self.worst_pair[2], 2),
            },
        }

    def line(self) -> str:
        return (f"  {self.total_bits:8.1f} bits over {self.informative_messages} "
                f"messages ({self.mean_bits_per_message:.3f} bits each) = "
                f"{self.disclosure_fraction:.1%} of a fully transparent system")


def _consistent_levels(
    performative: Perf,
    content: dict[str, Any],
    capacity: int,
    reserve: int = 1,
) -> int:
    """How many occupancy levels remain possible after hearing this message."""
    levels = capacity + 1

    if performative is Perf.PROPOSE:
        # An explicit strain figure pins occupancy exactly. This is what the
        # centralised arms transmit, and it is the whole difference.
        if "strain" in content:
            return 1
        # Otherwise: "I have room" rules out the top `reserve + 1` levels.
        return max(1, levels - (reserve + 1))

    if performative is Perf.REFUSE:
        reason = content.get("reason", "")
        if reason == "at_self_protection_reserve":
            # Pins occupancy to the top few levels. The most revealing thing a
            # hospital can say, which is worth stating in the paper.
            return max(1, reserve + 1)
        if reason in ("no_specialty_capability", "resource_not_offered"):
            # Says nothing about occupancy -- it is a statement about the
            # service catalogue, which is public anyway.
            return levels
        if reason == "cannot_meet_clinical_deadline":
            # Pure geometry: travel time is public. No occupancy content.
            return levels
        return levels

    return levels                       # CFP, ACCEPT, REJECT carry no capacity


def measure_leakage(
    bus: MessageBus,
    hospitals: dict[str, Any],
    resource_key: str = "icu_bed",
) -> LeakageReport:
    """Quantify capacity disclosure across a completed run.

    `hospitals` maps id -> Hospital, used only for capacities and reserves --
    the ground truth is never consulted, only the shape of the state space.
    """
    report = LeakageReport()

    capacities: dict[str, int] = {}
    for hid, hospital in hospitals.items():
        pools = getattr(hospital, "resources", None)
        capacity = 0
        if pools is not None:
            for res, pool in pools.pools.items():
                if res.value == resource_key:
                    capacity = pool.capacity
                    break
        capacities[hid] = max(1, capacity)

    pair_bits: dict[tuple[str, str], float] = {}

    for msg in bus.transcript:
        report.total_messages += 1
        capacity = capacities.get(msg.sender)
        if capacity is None:
            continue

        before = capacity + 1
        after = _consistent_levels(msg.performative, msg.content, capacity)
        if after >= before:
            continue

        bits = math.log2(before) - math.log2(after)
        report.total_bits += bits
        report.informative_messages += 1
        key = msg.performative.value
        report.bits_by_performative[key] = report.bits_by_performative.get(key, 0.0) + bits
        report.bits_per_hospital[msg.sender] = (
            report.bits_per_hospital.get(msg.sender, 0.0) + bits)
        pair = (msg.sender, msg.receiver)
        pair_bits[pair] = pair_bits.get(pair, 0.0) + bits

        # The ceiling: what this same message would have leaked had the sender
        # simply published its exact occupancy, as the centralised arms do.
        report.max_possible_bits += math.log2(before)

    if pair_bits:
        (target, observer), bits = max(pair_bits.items(), key=lambda kv: kv[1])
        report.worst_pair = (target, observer, bits)

    return report
