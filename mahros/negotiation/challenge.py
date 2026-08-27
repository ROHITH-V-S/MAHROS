"""Turning the audit ledger into evidence: how a refusal gets challenged.

In the original design the ledger was write-only. Agreements went in, nothing
ever came out, and the tamper-evidence demo was a party trick disconnected from
the protocol. This module is what makes the ledger load-bearing: it is the
*evidence base* a hospital's refusal is checked against.

Two kinds of evidence, because there are two kinds of claim
------------------------------------------------------------
The distinction that makes this work, and the one to put on a slide:

    **Capability is public. Capacity is private.**

Which hospitals run a cardiac unit, a cath lab, a neurosurgical service, is not
a secret -- it is on their website, in the regional service directory, and in
every referral guideline. How many ICU beds are free *right now* is genuinely
private, and MAHROS is built so it stays that way.

So the two claims are checked against two different things:

  ``no_specialty_capability``      "We don't do cardiac."
  ``resource_not_offered``         "We don't have a cath lab."
      Checked against the **public capability registry**. If the network
      directory lists this hospital as a cardiac centre, the claim is simply
      false, and everyone can see that it is false without anyone disclosing
      anything private. This is the cheapest and strongest check in the system.

  ``at_self_protection_reserve``   "We're down to our last bed."
      Checked against the **audit ledger**. Nobody can see this hospital's free
      beds -- but everyone can see what it has agreed to take. A hospital that
      says it is out of ICU beds and accepted a different ICU transfer twenty
      minutes later had a bed. It just preferred a different patient.

Note what this buys: the capacity check achieves accountability *without*
anyone revealing capacity. It works on the hospital's own past commitments, not
its current state. That is the property that lets the mechanism add
accountability without adding a data monopoly.

Everything else -- travel time, prep time, strain -- is either public geometry
or a private valuation. Public geometry needs no challenge (both sides compute
it identically). Private valuations cannot be challenged, and should not be:
a hospital is entitled to its own view of what a bed is worth.

The asymmetry that makes it work
--------------------------------
An honest refusal is always defensible, because the hospital's own state
supports it. A fabricated refusal has nothing behind it. So the mechanism does
not need to detect lies directly -- it only needs to *demand a justification*
and let the difference show. That is why a merely cautious hospital
(``DefensivePolicy``) is never wrongly punished, and the experiments measure
that false-accusation rate explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..core.types import Bid, TransferRequest

#: How far back the ledger is consulted, in simulation minutes. Four hours.
#: Long enough that a capability claim is genuinely contradicted; short enough
#: that "we had a bed this morning" is not treated as evidence about tonight.
LOOKBACK_MINUTES = 240.0

#: A reserve claim is only contradicted by an acceptance close in time.
#: Capacity really does change over a shift; it does not change over an hour
#: in a way that lets you accept one patient and plead emptiness for the next.
RESERVE_WINDOW_MINUTES = 60.0


@dataclass
class Evidence:
    """One ledger entry supporting a challenge, plus why it is relevant."""

    agreement_id: str
    receiver: str
    resource: str
    specialty: str
    agreed_at: float
    minutes_ago: float
    relevance: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "agreement_id": self.agreement_id,
            "receiver": self.receiver,
            "resource": self.resource,
            "specialty": self.specialty,
            "minutes_ago": round(self.minutes_ago, 1),
            "relevance": self.relevance,
        }


@dataclass
class Challenge:
    """A contradiction found between a stated refusal and the shared record."""

    target: str                    # the hospital whose refusal is challenged
    refusal_reason: str
    claim: str                     # plain English, spoken in the UI
    evidence: list[Evidence]

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(e.agreement_id for e in self.evidence)

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "refusal_reason": self.refusal_reason,
            "claim": self.claim,
            "evidence": [e.as_dict() for e in self.evidence],
        }


class ChallengeEngine:
    """Reads the ledger and finds refusals the record contradicts.

    Holds no private state and makes no clinical judgement. It compares public
    statements against a public record -- which is the only kind of check a
    decentralised system can perform without becoming the central authority it
    is trying to avoid. That property is worth stating in the paper: the
    challenge mechanism adds accountability *without* adding a data monopoly.
    """

    def __init__(
        self,
        ledger,
        registry: dict[str, set[str]] | None = None,
        resource_registry: dict[str, set[str]] | None = None,
        lookback: float = LOOKBACK_MINUTES,
    ) -> None:
        self.ledger = ledger
        #: hospital_id -> declared specialties. Public directory information.
        #: Empty means "no directory available", and capability claims then fall
        #: back to ledger evidence only -- a weaker but still usable check.
        self.registry = registry or {}
        #: hospital_id -> resources the facility is listed as operating.
        self.resource_registry = resource_registry or {}
        self.lookback = lookback
        self.raised = 0
        self.by_reason: dict[str, int] = {}

    def challenges_for(
        self, bid: Bid, req: TransferRequest, now: float
    ) -> list[Challenge]:
        """Find every contradiction between this refusal and the ledger."""
        if bid.feasible or not bid.refusal_reason:
            return []

        # Registry checks need no ledger; ledger checks obviously do.
        registry_only = bid.refusal_reason in (
            "no_specialty_capability", "resource_not_offered")
        if not registry_only and (
                self.ledger is None or not hasattr(self.ledger, "accepted_by")):
            return []

        handler = {
            "no_specialty_capability": self._challenge_capability,
            "resource_not_offered": self._challenge_resource,
            "at_self_protection_reserve": self._challenge_reserve,
        }.get(bid.refusal_reason)
        if handler is None:
            return []

        found = handler(bid, req, now)
        for challenge in found:
            self.raised += 1
            self.by_reason[challenge.refusal_reason] = (
                self.by_reason.get(challenge.refusal_reason, 0) + 1)
        return found

    # -- the three checks -------------------------------------------------- #
    def _challenge_capability(
        self, bid: Bid, req: TransferRequest, now: float
    ) -> list[Challenge]:
        """"We don't do that specialty" -- checked against the public directory.

        A hospital listed as a cardiac centre cannot claim to have no cardiac
        capability. That is not a matter of opinion or of private state; it is
        a matter of public record, and the contradiction is visible to every
        participant without anybody disclosing anything.
        """
        declared = self.registry.get(bid.bidder)
        if declared is not None and req.specialty.value in declared:
            return [Challenge(
                target=bid.bidder,
                refusal_reason=bid.refusal_reason,
                claim=(f"{bid.bidder} says it cannot treat {req.specialty.value} "
                       f"patients, but it is listed in the network directory as a "
                       f"{req.specialty.value} centre."),
                evidence=[Evidence(
                    agreement_id=f"registry:{bid.bidder}",
                    receiver=bid.bidder, resource="-",
                    specialty=req.specialty.value, agreed_at=now, minutes_ago=0.0,
                    relevance="declared capability in the public network directory",
                )],
            )]

        # No directory entry: fall back to what it has actually accepted.
        if not hasattr(self.ledger, "accepted_by"):
            return []
        rows = self.ledger.accepted_by(
            bid.bidder, since=now - self.lookback, specialty=req.specialty.value)
        if not rows:
            return []
        ev = self._evidence(
            rows[:3], now,
            f"accepted a {req.specialty.value} patient, so it has the capability")
        return [Challenge(
            target=bid.bidder,
            refusal_reason=bid.refusal_reason,
            claim=(f"{bid.bidder} says it cannot treat {req.specialty.value} patients, "
                   f"but the shared record shows it accepted one "
                   f"{ev[0].minutes_ago:.0f} minutes ago."),
            evidence=ev,
        )]

    def _challenge_resource(
        self, bid: Bid, req: TransferRequest, now: float
    ) -> list[Challenge]:
        """"We don't have that facility" -- also a matter of public record."""
        nice = req.resource.value.replace("_", " ")
        declared = self.resource_registry.get(bid.bidder)
        if declared is not None and req.resource.value in declared:
            return [Challenge(
                target=bid.bidder,
                refusal_reason=bid.refusal_reason,
                claim=(f"{bid.bidder} says it does not have a {nice}, but the network "
                       f"directory lists one at that site."),
                evidence=[Evidence(
                    agreement_id=f"registry:{bid.bidder}",
                    receiver=bid.bidder, resource=req.resource.value,
                    specialty="-", agreed_at=now, minutes_ago=0.0,
                    relevance="declared facility in the public network directory",
                )],
            )]

        if not hasattr(self.ledger, "accepted_by"):
            return []
        rows = self.ledger.accepted_by(
            bid.bidder, since=now - self.lookback, resource=req.resource.value)
        if not rows:
            return []
        ev = self._evidence(
            rows[:3], now, f"accepted a patient needing a {nice}, so it has one")
        return [Challenge(
            target=bid.bidder,
            refusal_reason=bid.refusal_reason,
            claim=(f"{bid.bidder} says it does not offer a {nice}, but the shared "
                   f"record shows it accepted a {nice} transfer "
                   f"{ev[0].minutes_ago:.0f} minutes ago."),
            evidence=ev,
        )]

    def _challenge_reserve(
        self, bid: Bid, req: TransferRequest, now: float
    ) -> list[Challenge]:
        """"We're down to our last bed" -- checked two ways.

        **Direct contradiction.** It accepted the same resource within the
        hour. It had one to give.

        **Broken commitments.** It has said this before and then, minutes
        later, taken a patient it preferred. That does not prove this
        particular refusal is false -- capacity is real and it may well be full
        today -- so the mechanism does not convict on it. It asks the hospital
        to justify itself. A hospital that is genuinely full justifies it in
        one step and the challenge falls. That asymmetry is the whole design:
        **a hospital that has broken its word before is asked to explain; one
        that has not is taken at its word.**
        """
        nice = req.resource.value.replace("_", " ")

        rows = self.ledger.accepted_by(
            bid.bidder, since=now - RESERVE_WINDOW_MINUTES,
            resource=req.resource.value)
        if rows:
            ev = self._evidence(
                rows[:2], now, f"took another {nice} patient within the hour")
            return [Challenge(
                target=bid.bidder,
                refusal_reason=bid.refusal_reason,
                claim=(f"{bid.bidder} says it is down to its last {nice}, but it "
                       f"accepted a different {nice} transfer "
                       f"{ev[0].minutes_ago:.0f} minutes ago. It had one to give."),
                evidence=ev,
            )]

        broken = self.ledger.broken_commitments(
            bid.bidder, window_minutes=RESERVE_WINDOW_MINUTES,
            since=now - self.lookback)
        if broken:
            refusal, accept = broken[0]
            gap = accept.get("agreed_at", 0.0) - refusal.get("agreed_at", 0.0)
            ev = [Evidence(
                agreement_id=accept.get("agreement_id", ""),
                receiver=bid.bidder,
                resource=accept.get("resource", ""),
                specialty=accept.get("specialty", ""),
                agreed_at=accept.get("agreed_at", 0.0),
                minutes_ago=max(0.0, now - accept.get("agreed_at", 0.0)),
                relevance=(f"declined a {accept.get('resource', '')} patient, then "
                           f"accepted one {gap:.0f} minutes later"),
            )]
            return [Challenge(
                target=bid.bidder,
                refusal_reason=bid.refusal_reason,
                claim=(f"{bid.bidder} says it is down to its last {nice}. It has said "
                       f"that {len(broken)} time(s) recently and then accepted a "
                       f"patient of the same kind minutes later. It is asked to "
                       f"justify this refusal."),
                evidence=ev,
            )]
        return []

    # -- helpers ----------------------------------------------------------- #
    @staticmethod
    def _evidence(rows: list[dict], now: float, relevance: str) -> list[Evidence]:
        return [
            Evidence(
                agreement_id=r.get("agreement_id", ""),
                receiver=r.get("receiver", ""),
                resource=r.get("resource", ""),
                specialty=r.get("specialty", ""),
                agreed_at=r.get("agreed_at", 0.0),
                minutes_ago=max(0.0, now - r.get("agreed_at", 0.0)),
                relevance=relevance,
            )
            for r in rows
        ]

    def stats(self) -> dict[str, Any]:
        return {
            "challenges_raised": self.raised,
            "challenges_by_reason": dict(self.by_reason),
            "lookback_minutes": self.lookback,
        }
