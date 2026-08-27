"""Layer 2b: argumentation. Where hospitals stop bidding and start arguing.

Why this exists
---------------
A plain Contract Net is a sealed-bid auction: everyone answers once, the best
answer wins, nobody is ever contradicted. That is fine if every hospital tells
the truth. Real hospitals have a standing incentive not to: accepting a
transfer costs a bed, a nurse, and a night. The cheapest way to avoid one is to
say "sorry, we can't" -- and in a sealed-bid protocol, nothing can check that.

So MAHROS lets a refusal be *challenged*. A hospital that refuses makes a
claim. The claim is checked against the shared tamper-evident ledger. If the
record contradicts it -- the hospital said it has no cardiac capability, but it
accepted a cardiac patient forty minutes ago -- the origin raises a challenge,
and the refusing hospital must either defend it or have it struck out.

The resolution rule is not ad hoc. It is Dung's grounded semantics [Dung 1995],
the standard formal account of which arguments survive a dispute, used here
exactly as specified. That matters for the paper: the mechanism is a known,
citable formalism applied to a new problem, not a bespoke heuristic.

How to explain it in one breath
-------------------------------
    Three arguments. A hospital refuses (A). The origin challenges the refusal
    with evidence from the ledger (B, which attacks A). The hospital defends
    itself (C, which attacks B). Because C is unattacked, C stands; because C
    defeats B, B falls; because B has fallen, A stands again -- the refusal is
    reinstated. If the hospital had no defence, B would stand, A would fall,
    and the refusal would be struck out.

That is the textbook reinstatement example, and it is the whole mechanism.

Formal note
-----------
An argumentation framework is a directed graph (arguments, attacks). The
*grounded extension* is the least fixed point of the characteristic function --
equivalently, the set of arguments labelled IN by the unique grounded
labelling. It is always unique and always computable in polynomial time, which
is why we use it rather than preferred semantics: a clinical system cannot
return "here are four equally valid worldviews, pick one".

Reference implementation cross-checked against PyArg (Odekerken & Borg, COMMA
2022) in `tests/test_argumentation.py`.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Iterable


class ArgKind(str, enum.Enum):
    """The moves a hospital is allowed to make. Deliberately few."""

    BID = "bid"                        # "I can take this patient."
    REFUSAL = "refusal"                # "I cannot take this patient, because R."
    COUNTER_OFFER = "counter_offer"    # "I can, but later / on conditions."
    CHALLENGE = "challenge"            # "The record contradicts that refusal."
    DEFENCE = "defence"                # "No it does not, and here is why."
    BURDEN_OBJECTION = "burden_objection"   # "Do not pick me, I am over my share."


class Label(str, enum.Enum):
    """Grounded labelling. IN = survives, OUT = defeated, UNDEC = unresolved."""

    IN = "in"
    OUT = "out"
    UNDEC = "undecided"


@dataclass(frozen=True)
class Argument:
    """One claim made by one participant during one negotiation.

    `speaker` is the hospital making the claim; `subject` is the hospital the
    claim is *about* (identical for a bid or refusal, different for a
    challenge). `evidence` carries the ledger entries that back a challenge, so
    the audit trail records not just who won an argument but on what basis.
    """

    arg_id: str
    kind: ArgKind
    speaker: str
    subject: str
    claim: str                              # plain English, shown in the UI
    machine_reason: str = ""                # the enum-ish reason code
    evidence: tuple[str, ...] = ()          # ledger agreement ids
    weight: float = 1.0

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<{self.kind.value} {self.arg_id} by {self.speaker}>"


@dataclass
class ArgumentationFramework:
    """A Dung framework: arguments plus an attack relation.

    Attacks are stored as (attacker_id, target_id) pairs. Nothing here knows
    anything about hospitals -- that separation is deliberate, so the semantics
    can be tested against the argumentation literature independently of the
    clinical model.
    """

    arguments: dict[str, Argument] = field(default_factory=dict)
    attacks: set[tuple[str, str]] = field(default_factory=set)

    # -- construction ------------------------------------------------------ #
    def add(self, argument: Argument) -> Argument:
        self.arguments[argument.arg_id] = argument
        return argument

    def attack(self, attacker: str, target: str) -> None:
        """Record that `attacker` attacks `target`.

        Self-attack is silently ignored: a hospital contradicting itself is a
        data bug, not a philosophical position, and admitting it would make the
        grounded extension undecided for no useful reason.
        """
        if attacker == target:
            return
        if attacker in self.arguments and target in self.arguments:
            self.attacks.add((attacker, target))

    # -- queries ----------------------------------------------------------- #
    def attackers_of(self, arg_id: str) -> set[str]:
        return {a for a, t in self.attacks if t == arg_id}

    def targets_of(self, arg_id: str) -> set[str]:
        return {t for a, t in self.attacks if a == arg_id}

    # -- semantics --------------------------------------------------------- #
    def grounded_labelling(self) -> dict[str, Label]:
        """Compute the unique grounded labelling.

        Fixed-point construction, straight from the definition:

          * an argument is IN once every one of its attackers is OUT
            (vacuously true for an unattacked argument, which seeds the loop)
          * an argument is OUT once any one of its attackers is IN
          * whatever never settles is UNDEC -- typically an even-length attack
            cycle, e.g. two hospitals each calling the other a liar with no
            independent evidence either way

        Terminates because each pass only ever moves arguments out of UNDEC,
        and there are finitely many arguments.
        """
        labels: dict[str, Label] = {a: Label.UNDEC for a in self.arguments}
        attackers = {a: self.attackers_of(a) for a in self.arguments}

        changed = True
        while changed:
            changed = False
            for arg_id in self.arguments:
                if labels[arg_id] is not Label.UNDEC:
                    continue
                if all(labels[x] is Label.OUT for x in attackers[arg_id]):
                    labels[arg_id] = Label.IN
                    changed = True
            for arg_id in self.arguments:
                if labels[arg_id] is not Label.UNDEC:
                    continue
                if any(labels[x] is Label.IN for x in attackers[arg_id]):
                    labels[arg_id] = Label.OUT
                    changed = True
        return labels

    def grounded_extension(self) -> set[str]:
        """The argument ids that survive the dispute."""
        return {a for a, lab in self.grounded_labelling().items() if lab is Label.IN}

    # -- reporting --------------------------------------------------------- #
    def surviving(self, kind: ArgKind) -> list[Argument]:
        ext = self.grounded_extension()
        return [self.arguments[a] for a in sorted(ext)
                if self.arguments[a].kind is kind]

    def defeated(self, kind: ArgKind) -> list[Argument]:
        labels = self.grounded_labelling()
        return [self.arguments[a] for a in sorted(self.arguments)
                if self.arguments[a].kind is kind and labels[a] is Label.OUT]

    def transcript(self) -> list[dict]:
        """Everything the UI and the ledger need, in speaking order."""
        labels = self.grounded_labelling()
        out = []
        for arg_id, arg in self.arguments.items():
            out.append({
                "id": arg_id,
                "kind": arg.kind.value,
                "speaker": arg.speaker,
                "subject": arg.subject,
                "claim": arg.claim,
                "machine_reason": arg.machine_reason,
                "evidence": list(arg.evidence),
                "label": labels[arg_id].value,
                "attacks": sorted(self.targets_of(arg_id)),
                "attacked_by": sorted(self.attackers_of(arg_id)),
            })
        return out

    def summary(self) -> dict:
        labels = self.grounded_labelling()
        counts: dict[str, int] = {}
        for lab in labels.values():
            counts[lab.value] = counts.get(lab.value, 0) + 1
        return {
            "n_arguments": len(self.arguments),
            "n_attacks": len(self.attacks),
            "labels": counts,
            "refusals_upheld": len(self.surviving(ArgKind.REFUSAL)),
            "refusals_struck_out": len(self.defeated(ArgKind.REFUSAL)),
            "challenges_upheld": len(self.surviving(ArgKind.CHALLENGE)),
        }


def build_attacks(framework: ArgumentationFramework) -> None:
    """Apply the MAHROS attack rulebook to a framework of stated arguments.

    Four rules, and only four. Each is a sentence a clinician would accept:

      1. A challenge attacks the refusal it names.
         *"You said you couldn't; the record says otherwise."*
      2. A defence attacks the challenge it answers.
         *"The record doesn't say what you think it says."*
      3. A burden objection attacks that hospital's own bid.
         *"Don't send it here, we're already over our share."*
      4. A counter-offer attacks that hospital's own original bid.
         *"Forget my first answer, here is my real one."*

    Everything else in the protocol -- speed, capability, strain -- is scored
    numerically, not argued. Argumentation is reserved for claims that can be
    *contradicted by evidence*, which is what makes the mechanism meaningful
    rather than a debate club.
    """
    by_subject: dict[str, list[Argument]] = {}
    for arg in framework.arguments.values():
        by_subject.setdefault(arg.subject, []).append(arg)

    for subject, args in by_subject.items():
        refusals = [a for a in args if a.kind is ArgKind.REFUSAL]
        challenges = [a for a in args if a.kind is ArgKind.CHALLENGE]
        defences = [a for a in args if a.kind is ArgKind.DEFENCE]
        bids = [a for a in args if a.kind is ArgKind.BID]
        objections = [a for a in args if a.kind is ArgKind.BURDEN_OBJECTION]
        counters = [a for a in args if a.kind is ArgKind.COUNTER_OFFER]

        for challenge in challenges:
            for refusal in refusals:
                framework.attack(challenge.arg_id, refusal.arg_id)      # rule 1
            for defence in defences:
                framework.attack(defence.arg_id, challenge.arg_id)      # rule 2
        for objection in objections:
            for bid in bids:
                framework.attack(objection.arg_id, bid.arg_id)          # rule 3
        for counter in counters:
            for bid in bids:
                framework.attack(counter.arg_id, bid.arg_id)            # rule 4


def framework_from(arguments: Iterable[Argument]) -> ArgumentationFramework:
    """Convenience: build a framework and wire up the standard attack rules."""
    af = ArgumentationFramework()
    for arg in arguments:
        af.add(arg)
    build_attacks(af)
    return af
