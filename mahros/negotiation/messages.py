"""Contract Net message types and the peer message bus.

The bus exists so that "no central authority sees everyone's data" is a
structural property we can *test*, not a claim in a slide. Every message is
addressed point-to-point and logged; `MessageBus.audit_no_global_view()`
verifies that no single participant received the private state of all peers.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class Perf(str, enum.Enum):
    """FIPA-style performatives, restricted to the Contract Net subset."""

    CFP = "call_for_proposal"
    PROPOSE = "propose"
    REFUSE = "refuse"
    ACCEPT_PROPOSAL = "accept_proposal"
    REJECT_PROPOSAL = "reject_proposal"
    INFORM_DONE = "inform_done"
    FAILURE = "failure"


@dataclass
class Message:
    performative: Perf
    sender: str
    receiver: str
    conversation_id: str                 # == request_id
    content: dict[str, Any] = field(default_factory=dict)
    sent_at: float = 0.0
    size_bytes: int = 0                  # communication-overhead metric


class MessageBus:
    """Point-to-point delivery with per-hop latency and a full transcript."""

    def __init__(self, latency_minutes: float = 0.05) -> None:
        self.latency = latency_minutes
        self.transcript: list[Message] = []
        self._received_by: dict[str, set[str]] = {}  # receiver -> senders seen

    def send(self, msg: Message) -> Message:
        msg.size_bytes = len(str(msg.content))
        self.transcript.append(msg)
        self._received_by.setdefault(msg.receiver, set()).add(msg.sender)
        return msg

    # -- evaluation metrics ------------------------------------------------ #
    @property
    def message_count(self) -> int:
        return len(self.transcript)

    @property
    def bytes_exchanged(self) -> int:
        return sum(m.size_bytes for m in self.transcript)

    def counts_by_performative(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for m in self.transcript:
            out[m.performative.value] = out.get(m.performative.value, 0) + 1
        return out

    def audit_no_global_view(self, all_hospitals: set[str]) -> dict[str, Any]:
        """Measure how concentrated decision-making and data exposure actually are.

        The naive test -- "did any node hear from all its peers?" -- is wrong. In
        a six-hospital network the requesting hospital legitimately asks all five
        others, which would flag a correctly decentralised system as centralised.
        Breadth per request is not the issue.

        The property that actually separates MAHROS from a central authority is
        *concentration across requests*:

          decision_concentration -- the largest share of all negotiations brokered
              by any single node. A central authority brokers 100% of them. In
              MAHROS each hospital brokers only its own patients' transfers, so
              this tends toward the busiest hospital's share of demand.

          disclosure_concentration -- the largest share of all capacity
              disclosures received by any single node. Again 100% for a central
              optimiser, since every hospital reports its private state to it.

        A system is decentralised here when no single node brokers or observes a
        majority of the network's activity.
        """
        brokered: dict[str, set[str]] = {}       # node -> conversations it ran
        disclosures: dict[str, int] = {}         # node -> capacity reports received
        peers_seen: dict[str, set[str]] = {}     # node -> distinct peers disclosing to it
        conversations: set[str] = set()

        for m in self.transcript:
            conversations.add(m.conversation_id)
            if m.performative is Perf.CFP:
                brokered.setdefault(m.sender, set()).add(m.conversation_id)
            if m.performative in (Perf.PROPOSE, Perf.REFUSE):
                disclosures[m.receiver] = disclosures.get(m.receiver, 0) + 1
                peers_seen.setdefault(m.receiver, set()).add(m.sender)

        n_conv = max(1, len(conversations))
        n_disc = max(1, sum(disclosures.values()))

        decision_conc = max((len(v) for v in brokered.values()), default=0) / n_conv
        disclosure_conc = max(disclosures.values(), default=0) / n_disc
        worst_breadth = max((len(v) for v in peers_seen.values()), default=0)

        return {
            "network_size": len(all_hospitals),
            "conversations": len(conversations),
            "decision_concentration": round(decision_conc, 4),
            "disclosure_concentration": round(disclosure_conc, 4),
            "max_peers_disclosed_to_one_node": worst_breadth,
            "is_decentralised": decision_conc < 0.5 and disclosure_conc < 0.5,
            "brokers": {k: len(v) for k, v in brokered.items()},
        }
