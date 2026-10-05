"""Collusion: the attack this mechanism was most exposed to, and a defence.

The project's own limitations section named this the most important gap, and it
is the right thing to attack next, because the obvious version of it defeats the
existing mechanism completely.

The attack, and why it works
----------------------------
The ledger-backed challenge finds one specific thing: a hospital that *refused*
a resource and then *accepted* that same resource shortly afterwards. Both
halves have to come from the same hospital, which is exactly what makes the
evidence strong -- nobody had to be trusted, and no private state was revealed.

That strength is also the weakness. A cartel does not need to lie better. It
needs only to **stop producing the contradiction**:

    While any member is refusing resource R, no member accepts R from outside
    the cartel.

Every refusal is then individually unimpeachable. The hospital said it had no
ICU bed and, on the public record, never took an ICU patient. There is nothing
to challenge, no matter how thorough the challenge engine is. The mechanism is
not defeated by a better lie; it is defeated by the removal of evidence.

Coordinated abstention is not free, and that matters. A cartel member that
would have accepted a patient must now refuse it to protect the story, giving up
capacity it wanted to use. `Cartel.abstentions` counts that cost, and the
experiment reports it: collusion buys protection from challenge at the price of
turning away patients the cartel could have served.

What the cartel *cannot* do
---------------------------
Two things, and both are design consequences worth stating.

**It cannot forge a capability claim.** "We don't run a cath lab" is checked
against the public service directory. No amount of mutual corroboration changes
what the directory says, because the directory is not a party to the cartel.

**It cannot co-sign each other's attestations.** The attestation co-signer is
deliberately a party the hospital does not control -- its HMS vendor or a
regulator's registry -- precisely so that "my friend vouches for me" is not a
move in this protocol. Had the co-signer been a peer hospital, a cartel would
break attestation outright.

The defence, and its limits
---------------------------
Individual refusals inside a cartel are individually defensible. The *joint
pattern* is not. So the third evidence tier is different in kind from the first
two:

    registry  -> a public fact          (unilateral, logical)
    ledger    -> a bilateral commitment (bilateral, logical)
    co-refusal-> a multilateral pattern (statistical)

`CoRefusalDetector` measures how much more often a pair of hospitals refuses the
same request than their individual refusal rates predict. Under independence the
lift is about 1; a cartel drives it well above.

**The confound is severe and must not be waved away.** During a surge, hospitals
genuinely do refuse together, because they are genuinely all full at once. A
naive detector accuses an entire region of collusion at the exact moment the
network is under most strain -- when its refusals are most honest. The detector
therefore reports lift against a **matched-load baseline**, and the experiment
calibrates the alarm threshold on an all-honest network under the same scenario,
where every alarm is by construction a false positive.

Like the audit referrals, a co-refusal alarm is **advisory and never overrules
anyone**. It cannot be: it is evidence about a group, and no individual patient's
transfer should turn on a statistic about somebody else.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any


# --------------------------------------------------------------------------- #
# The attack
# --------------------------------------------------------------------------- #

@dataclass
class Cartel:
    """Shared state for a coalition of hospitals coordinating their refusals.

    One instance is shared by every member policy. It is deliberately *not* a
    communication channel the protocol provides -- a real cartel coordinates out
    of band, by telephone, and this object models that side channel rather than
    any part of MAHROS.
    """

    members: frozenset[str] = frozenset()
    #: How long a member's refusal obliges the others to abstain, in minutes.
    #: Matches the challenge engine's reserve window: abstaining for longer than
    #: the window buys the cartel nothing, and costs it more patients.
    abstain_window: float = 60.0

    #: resource -> most recent time any member refused it.
    _last_refusal: dict[str, float] = field(default_factory=dict)

    #: Patients a member turned away purely to protect the cartel's story.
    abstentions: int = 0
    #: Refusals coordinated in total.
    coordinated_refusals: int = 0

    def note_refusal(self, resource: str, now: float) -> None:
        self._last_refusal[resource] = max(
            now, self._last_refusal.get(resource, float("-inf")))
        self.coordinated_refusals += 1

    def must_abstain(self, resource: str, now: float) -> bool:
        """Would accepting this resource now contradict a member's refusal?

        This is the whole attack in one predicate. If a member said "no ICU bed"
        forty minutes ago, nobody in the cartel may be seen taking an ICU
        patient, or the ledger acquires exactly the contradiction the challenge
        engine is looking for.
        """
        last = self._last_refusal.get(resource)
        if last is None:
            return False
        return 0.0 <= (now - last) <= self.abstain_window

    def note_abstention(self) -> None:
        self.abstentions += 1

    def stats(self) -> dict[str, Any]:
        return {
            "members": sorted(self.members),
            "size": len(self.members),
            "coordinated_refusals": self.coordinated_refusals,
            "abstentions": self.abstentions,
            "abstain_window_minutes": self.abstain_window,
        }


# --------------------------------------------------------------------------- #
# The defence
# --------------------------------------------------------------------------- #

@dataclass
class CoRefusalAlarm:
    """A pair of hospitals refusing together more than chance explains.

    **Not a finding of collusion.** Hospitals in one metro serve one population
    and fill up together; the pattern has an innocent explanation and usually
    has it. The alarm names a pair worth looking at, carries the measured
    false-alarm rate, and never affects a live decision.
    """

    pair: tuple[str, str]
    joint_refusals: int
    opportunities: int
    observed_rate: float
    expected_rate: float
    lift: float
    false_alarm_rate: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "pair": list(self.pair),
            "joint_refusals": self.joint_refusals,
            "opportunities": self.opportunities,
            "observed_rate": round(self.observed_rate, 4),
            "expected_rate": round(self.expected_rate, 4),
            "lift": round(self.lift, 3),
            "false_alarm_rate": round(self.false_alarm_rate, 4),
            "status": "advisory -- NOT a finding of collusion",
        }


class CoRefusalDetector:
    """Tracks refusal dependence between hospitals, across requests.

    Two statistics are maintained, and the difference between them is a result
    in its own right.

    **Same-request lift** (``lift``) asks whether two hospitals refuse the same
    patient together more than their individual rates predict. It is the obvious
    measure and it **does not detect coordinated abstention** -- see
    ``docs/`` and the module docstring. Kept because a reviewer will ask why not,
    and because reporting a measure that failed is cheaper than defending its
    absence.

    **Temporal follow-on** (``follow_on_lift``) asks the question the attack
    actually poses: after A refuses resource R, does B's refusal rate for R rise
    above its own baseline? Coordinated abstention is exactly this dependence,
    so this is where its signature lives.

    Both are O(bidders^2) integer updates per request -- a few hundred
    increments at a metro's scale, cheap enough to maintain live. Neither is
    consulted during a negotiation.
    """

    def __init__(self, min_opportunities: int = 30,
                 follow_window: float = 60.0) -> None:
        #: Requests where both hospitals were asked.
        self._pair_opportunities: dict[tuple[str, str], int] = {}
        #: ...and both refused.
        self._pair_refusals: dict[tuple[str, str], int] = {}
        self._asked: dict[str, int] = {}
        self._refused: dict[str, int] = {}

        #: (resource) -> hospital -> time of that hospital's last refusal.
        self._last_refusal: dict[str, dict[str, float]] = {}
        #: (a, b) -> times b was asked while a had refused that resource
        #: recently, and how often b then refused too.
        self._follow_opportunities: dict[tuple[str, str], int] = {}
        self._follow_refusals: dict[tuple[str, str], int] = {}

        #: A pair seen fewer times than this is not scored: a ratio on four
        #: observations is noise, and reporting it would bury the real signal.
        self.min_opportunities = min_opportunities
        #: How long one hospital's refusal is treated as possibly influencing
        #: another's. Matches the cartel's abstention window.
        self.follow_window = follow_window
        self.requests_seen = 0

    def observe(self, asked: list[str], refused: list[str],
                now: float = 0.0, resource: str = "") -> None:
        """Record one completed call for proposals."""
        self.requests_seen += 1
        refused_set = set(refused)
        recent = self._last_refusal.setdefault(resource, {})

        for hid in asked:
            self._asked[hid] = self._asked.get(hid, 0) + 1
            if hid in refused_set:
                self._refused[hid] = self._refused.get(hid, 0) + 1

        # -- same-request co-refusal (the measure that does not work) ------ #
        for a, b in itertools.combinations(sorted(set(asked)), 2):
            key = (a, b)
            self._pair_opportunities[key] = self._pair_opportunities.get(key, 0) + 1
            if a in refused_set and b in refused_set:
                self._pair_refusals[key] = self._pair_refusals.get(key, 0) + 1

        # -- temporal follow-on (the measure that does) -------------------- #
        # For every hospital asked now, which *other* hospitals refused this
        # same resource recently? If B is systematically refusing in the wake of
        # A's refusals, that is the abstention pact showing itself.
        for b in set(asked):
            for a, when in recent.items():
                if a == b or not (0.0 <= now - when <= self.follow_window):
                    continue
                key = (a, b)
                self._follow_opportunities[key] = \
                    self._follow_opportunities.get(key, 0) + 1
                if b in refused_set:
                    self._follow_refusals[key] = \
                        self._follow_refusals.get(key, 0) + 1

        for hid in refused_set:
            recent[hid] = now

    # -- marginals --------------------------------------------------------- #
    def refusal_rate(self, hid: str) -> float:
        asked = self._asked.get(hid, 0)
        return (self._refused.get(hid, 0) / asked) if asked else 0.0

    # -- statistic 1: same-request lift (retained, does not work) ---------- #
    def lift(self, a: str, b: str) -> float:
        """Observed joint-refusal rate over the rate independence predicts.

        Does not detect coordinated abstention. See the class docstring.
        """
        key = (a, b) if a <= b else (b, a)
        opportunities = self._pair_opportunities.get(key, 0)
        if opportunities < self.min_opportunities:
            return float("nan")
        observed = self._pair_refusals.get(key, 0) / opportunities
        expected = self.refusal_rate(a) * self.refusal_rate(b)
        if expected <= 0:
            return float("nan")
        return observed / expected

    # -- statistic 2: temporal follow-on ----------------------------------- #
    def follow_on_lift(self, a: str, b: str) -> float:
        """How much B's refusal rate rises in the wake of A refusing.

        1.0 means A's refusals tell you nothing about B's. Above 1 means B
        refuses more than its own baseline once A has refused -- which is what
        an abstention pact does, and also what a genuine region-wide surge does.
        The confound is real and the experiment calibrates against it.
        """
        key = (a, b)
        opportunities = self._follow_opportunities.get(key, 0)
        if opportunities < self.min_opportunities:
            return float("nan")
        conditional = self._follow_refusals.get(key, 0) / opportunities
        baseline = self.refusal_rate(b)
        if baseline <= 0:
            return float("nan")
        return conditional / baseline

    def alarms(self, threshold: float,
               false_alarm_rate: float = 0.0) -> list[CoRefusalAlarm]:
        """Ordered pairs whose follow-on dependence exceeds `threshold`.

        `threshold` is not a natural constant. It is calibrated against an
        all-honest network under the same scenario, where every alarm is a false
        alarm by construction -- see `experiments/collusion.py`.
        """
        out: list[CoRefusalAlarm] = []
        for key, opportunities in self._follow_opportunities.items():
            if opportunities < self.min_opportunities:
                continue
            a, b = key
            value = self.follow_on_lift(a, b)
            if math.isnan(value) or value < threshold:
                continue
            out.append(CoRefusalAlarm(
                pair=key,
                joint_refusals=self._follow_refusals.get(key, 0),
                opportunities=opportunities,
                observed_rate=self._follow_refusals.get(key, 0) / opportunities,
                expected_rate=self.refusal_rate(b),
                lift=value,
                false_alarm_rate=false_alarm_rate,
            ))
        out.sort(key=lambda x: -x.lift)
        return out

    def max_lift(self) -> float:
        """Highest follow-on dependence over all scored ordered pairs."""
        values = [v for v in (self.follow_on_lift(a, b)
                              for (a, b) in self._follow_opportunities)
                  if not math.isnan(v)]
        return max(values) if values else float("nan")

    def max_same_request_lift(self) -> float:
        """The measure that failed, kept so the negative result is reproducible."""
        values = [v for v in (self.lift(a, b)
                              for (a, b) in self._pair_opportunities)
                  if not math.isnan(v)]
        return max(values) if values else float("nan")

    def stats(self) -> dict[str, Any]:
        return {
            "requests_seen": self.requests_seen,
            "pairs_scored": sum(
                1 for v in self._follow_opportunities.values()
                if v >= self.min_opportunities),
            "max_follow_on_lift": self.max_lift(),
            "max_same_request_lift": self.max_same_request_lift(),
            "follow_window_minutes": self.follow_window,
        }
