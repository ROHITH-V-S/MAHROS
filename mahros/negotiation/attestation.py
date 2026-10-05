"""Attested refusal: signing the claim, not the bed count.

The gap this closes
-------------------
The ledger-backed challenge in `challenge.py` is *circumstantial*. It finds a
refusal the record contradicts -- "you declined an ICU patient and accepted one
twenty minutes later" -- and that works, but it needs a coincidence. A hospital
that refuses when no contradicting acceptance happens to follow is never
challenged at all, however dishonest the refusal was. The experiments show this
directly: most individual lies go undetected even though the success rate
recovers well.

The obvious fix is to make hospitals show their bed state. That fix is worse
than the problem. A hospital willing to lie in a message will lie in a database
query, so unattested disclosure moves the lie one layer down rather than
removing it -- and continuous disclosure rebuilds the central capacity registry
the whole design exists to avoid.

What this module does instead
-----------------------------
Three properties, and the combination is the point:

1. **Contest-triggered, never continuous.** A hospital is never asked for its
   capacity. Only when its refusal is *challenged* may it discharge the
   challenge by attesting. Nothing is disclosed in the common case.

2. **Minimal disclosure: the predicate, not the state.** The hospital does not
   sign "I have 20 of 20 ICU beds occupied". It signs exactly the proposition
   under contest -- *"no unit of this resource was available to this request at
   time T"* -- and nothing else. An observer learns only whether the contested
   claim was asserted under signature, which is precisely what it already
   learned from the refusal. The measurable consequence: attesting leaks **no
   additional bits** about occupancy beyond the refusal itself, which
   `mahros/privacy/leakage.py` can verify.

3. **Non-repudiable, and locally verifiable.** The attestation is co-signed --
   by the hospital and by a second key it does not solely control (its HMS
   vendor, or a regulator's registry key). Verification needs only published
   public keys and no round trip to any authority, so it costs microseconds and
   stays inside the clinical window. That is what makes it deployable rather
   than merely sound.

Why this changes behaviour rather than just detection
-----------------------------------------------------
The mechanism's value is not mainly that it catches more lies. It is that a
refusal which must be *signed* is a different act from one merely asserted. A
bed manager who would say "sorry, we're full" on the phone may decline to put a
co-signed, non-repudiable statement behind it, because a false attestation
contradicted by the hospital's own later acceptances is durable, attributable
evidence -- the thing a regulator or a court can act on.

So the model exposes `attestation_deterrence` as an explicit, swept parameter,
and the experiments report detection and deterrence separately. Deterrence is an
assumption about institutional behaviour, not something this or any dataset
measures, and it is labelled as one.

Cryptography, stated honestly
-----------------------------
True non-repudiation requires asymmetric signatures: with a shared secret the
verifier could forge what it verifies. This module therefore uses **Ed25519**
when `cryptography` is installed (``pip install -e ".[attest]"``), and falls
back to HMAC-SHA256 from the standard library otherwise, so the repository stays
dependency-free and reproducible offline.

The fallback models the *protocol* faithfully -- the same messages, the same
verification points, the same costs -- but does **not** provide real
non-repudiation. Every result in the paper concerns the mechanism's effect on
transfer outcomes, which is unchanged by the choice; no result claims
cryptographic strength. `signer_backend()` reports which is in use.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from typing import Any

#: The predicates a hospital may attest to. Deliberately few, and each is the
#: exact proposition some capacity refusal asserts -- so signing one discloses
#: nothing the refusal did not already say in public.
#:
#: There are two because a capacity refusal is genuinely two different claims:
#:
#:   NO_UNIT_AVAILABLE   "there is no free unit of this resource"
#:   AT_DECLARED_RESERVE "the only free units are inside the reserve this
#:                        hospital holds back as a matter of declared policy"
#:
#: Both can be entirely honest, and a hospital forced to certify the first when
#: the second is what it meant would be signing something false. An earlier
#: version had only the first predicate, and a cautious hospital asked to
#: certify either lied or declined -- and declining had it overruled, because a
#: reserve-holder's truthful bid is feasible. A liar has neither predicate
#: available, so the split costs nothing against deceit.
NO_UNIT_AVAILABLE = "no_unit_available"
AT_DECLARED_RESERVE = "at_declared_reserve"

#: Every predicate a well-formed attestation may carry.
PREDICATES = (NO_UNIT_AVAILABLE, AT_DECLARED_RESERVE)

#: How soon after an attested refusal an acceptance of the same resource is
#: worth *referring for audit*.
#:
#: This is not a proof threshold, and the number was chosen by measurement
#: rather than intuition. In a run where every signed attestation is true by
#: construction, the referral rate against those true attestations is:
#:
#:     60 min -> 11.2%    30 min -> 5.5%    15 min -> 1.9%
#:     10 min -> 1.0%      5 min -> 0.4%     1 min -> 0.2%
#:
#: All of those are false positives: a hospital full at T that admits at T+30
#: after a discharge has not lied. Five minutes keeps the rate under half a
#: percent while still catching the physically implausible cases. It does not
#: reach zero, and it cannot -- which is why referrals are advisory and never
#: overrule anyone.
AUDIT_REFERRAL_WINDOW_MINUTES = 5.0


def _try_ed25519():
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa
        return ed25519
    except Exception:
        return None


_ED = _try_ed25519()


def signer_backend() -> str:
    """Which signature backend is active. Reported in results, never assumed."""
    return "ed25519" if _ED is not None else "hmac-sha256"


def provides_non_repudiation() -> bool:
    """True only with asymmetric signatures.

    Called by the experiments so a run can state plainly whether its
    non-repudiation claim is real or modelled.
    """
    return _ED is not None


# --------------------------------------------------------------------------- #
# Keys
# --------------------------------------------------------------------------- #

class Keyring:
    """Signing keys for hospitals and the independent co-signer.

    The co-signer models the second party a hospital cannot unilaterally
    control -- in deployment its HMS/EHR vendor, or a regulator's registry. Its
    presence is what stops a hospital from quietly re-writing what it attested:
    two independent parties signed it, and only one of them benefits from the
    lie.
    """

    def __init__(self, seed: bytes | None = None) -> None:
        self._seed = seed or b"mahros-attestation"
        self._hospital: dict[str, Any] = {}
        self._cosigner = self._derive("cosigner")

    def _derive(self, label: str):
        material = hashlib.sha256(self._seed + label.encode()).digest()
        if _ED is not None:
            return _ED.Ed25519PrivateKey.from_private_bytes(material)
        return material

    def hospital_key(self, hospital: str):
        if hospital not in self._hospital:
            self._hospital[hospital] = self._derive(f"hospital:{hospital}")
        return self._hospital[hospital]

    def cosigner_key(self):
        return self._cosigner

    # -- primitive ------------------------------------------------------- #
    @staticmethod
    def _sign_with(key, payload: bytes) -> str:
        if _ED is not None:
            return key.sign(payload).hex()
        return hmac.new(key, payload, hashlib.sha256).hexdigest()

    @staticmethod
    def _verify_with(key, payload: bytes, signature: str) -> bool:
        if _ED is not None:
            try:
                key.public_key().verify(bytes.fromhex(signature), payload)
                return True
            except Exception:
                return False
        expected = hmac.new(key, payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)


# --------------------------------------------------------------------------- #
# The attestation
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class CapacityAttestation:
    """A signed assertion of the contested predicate, and nothing more.

    Note what is *absent*: no bed count, no occupancy, no census, no patient.
    `asserted` is a single predicate about a single resource at a single
    instant, for a single request.
    """

    hospital: str
    request_id: str
    resource: str
    asserted: str                 # always NO_UNIT_AVAILABLE in this protocol
    at: float
    nonce: str
    hospital_signature: str = ""
    cosigner_signature: str = ""

    def canonical(self) -> bytes:
        """Byte string that is actually signed. Order-stable."""
        return json.dumps({
            "hospital": self.hospital,
            "request_id": self.request_id,
            "resource": self.resource,
            "asserted": self.asserted,
            "at": round(self.at, 4),
            "nonce": self.nonce,
        }, sort_keys=True, separators=(",", ":")).encode()

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical()).hexdigest()[:16]

    def as_dict(self) -> dict[str, Any]:
        return {
            "hospital": self.hospital,
            "request_id": self.request_id,
            "resource": self.resource,
            "asserted": self.asserted,
            "at": round(self.at, 4),
            "digest": self.digest,
            "backend": signer_backend(),
            "co_signed": bool(self.cosigner_signature),
            # Stated on every record so a reader is never misled about what the
            # signature is worth in this run.
            "non_repudiation": provides_non_repudiation(),
        }


@dataclass
class AuditReferral:
    """A signed attestation worth checking against the hospital's own records.

    **This is not a finding of dishonesty, and must never be reported as one.**
    A hospital that was genuinely full when it signed, and admitted a patient
    shortly afterwards because a bed freed, produces exactly this pattern and
    has done nothing wrong. The measured false-positive rate is carried on every
    referral so the number cannot be quoted without its caveat.

    What the referral is actually worth: it names one signed, timestamped,
    co-signed proposition and the record that sits oddly beside it. A regulator
    with existing inspection rights can resolve it in one query against the
    hospital's own system. That is a far narrower ask than continuous
    disclosure, and a far sharper instrument than a complaint.
    """

    attestation: CapacityAttestation
    adjacent_agreement: str
    minutes_after: float
    claim: str
    false_positive_rate: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "hospital": self.attestation.hospital,
            "attestation_digest": self.attestation.digest,
            "resource": self.attestation.resource,
            "adjacent_agreement": self.adjacent_agreement,
            "minutes_after": round(self.minutes_after, 1),
            "claim": self.claim,
            "false_positive_rate": round(self.false_positive_rate, 4),
            "status": "referral_for_audit -- NOT a finding of dishonesty",
        }


#: Backwards-compatible alias. The old name overclaimed.
PerjuryFinding = AuditReferral


class AttestationAuthority:
    """Issues, verifies and later falsifies capacity attestations.

    Holds no capacity data and keeps no standing record of anyone's state. It
    only signs what a hospital chooses to assert when challenged, and checks
    signed assertions against the public ledger afterwards.
    """

    def __init__(self, keyring: Keyring | None = None) -> None:
        self.keyring = keyring or Keyring()
        self.issued = 0
        self.declined = 0
        self.verified = 0
        self.forged_rejected = 0
        self.referrals: list[AuditReferral] = []
        #: request_id -> attestations, so a later contradiction can be found.
        self._by_request: dict[str, list[CapacityAttestation]] = {}
        self._all: list[CapacityAttestation] = []

    # -- issue ------------------------------------------------------------ #
    def attest(
        self,
        hospital: str,
        request_id: str,
        resource: str,
        at: float,
        asserted: str = NO_UNIT_AVAILABLE,
        nonce: str | None = None,
    ) -> CapacityAttestation:
        """Co-sign the contested predicate for one hospital and one request."""
        if asserted not in PREDICATES:
            raise ValueError(f"unknown predicate {asserted!r}")
        att = CapacityAttestation(
            hospital=hospital, request_id=request_id, resource=resource,
            asserted=asserted, at=at,
            nonce=nonce or os.urandom(8).hex(),
        )
        payload = att.canonical()
        signed = CapacityAttestation(
            **{**att.__dict__,
               "hospital_signature": Keyring._sign_with(
                   self.keyring.hospital_key(hospital), payload),
               "cosigner_signature": Keyring._sign_with(
                   self.keyring.cosigner_key(), payload)},
        )
        self.issued += 1
        self._by_request.setdefault(request_id, []).append(signed)
        self._all.append(signed)
        return signed

    def record_declined(self) -> None:
        """A hospital was asked to attest and would not."""
        self.declined += 1

    # -- verify ----------------------------------------------------------- #
    def verify(self, att: CapacityAttestation) -> bool:
        """Check both signatures. Local, offline, no authority round trip.

        This is the operation that has to be fast enough to sit inside a live
        negotiation, so it is deliberately two signature checks and nothing
        else -- no database, no network, no consensus.
        """
        payload = att.canonical()
        ok = (
            Keyring._verify_with(self.keyring.hospital_key(att.hospital),
                                 payload, att.hospital_signature)
            and Keyring._verify_with(self.keyring.cosigner_key(),
                                     payload, att.cosigner_signature)
            and att.asserted in PREDICATES
        )
        if ok:
            self.verified += 1
        else:
            self.forged_rejected += 1
        return ok

    # -- falsify, after the fact ------------------------------------------ #
    def audit(self, ledger, now: float,
              window: float = AUDIT_REFERRAL_WINDOW_MINUTES,
              false_positive_rate: float = 0.0) -> list[AuditReferral]:
        """Attestations sitting oddly beside the attester's own later record.

        Returns **referrals for audit, not findings of dishonesty.** Public data
        cannot establish that a capacity attestation was false: a hospital that
        was full when it signed and admitted a patient minutes later after a
        discharge produces the same pattern as one that lied.

        What this is for: it hands a regulator a specific signed proposition,
        with a timestamp and a co-signature, that can be resolved in one query
        against records it is already entitled to inspect. The alternative --
        standing access to every hospital's live bed state -- is the central
        registry this whole design exists to avoid.

        Never called in the decision path. Overruling happens live and on the
        ledger-backed challenge alone, so the mechanism's zero-false-accusation
        property is unaffected by anything here.
        """
        found: list[AuditReferral] = []
        for att in self._all:
            # Only "no unit available" is contradicted by taking a patient.
            # A hospital that said its free units were inside its declared
            # reserve has admitted having one, so accepting later contradicts
            # nothing.
            if att.asserted != NO_UNIT_AVAILABLE:
                continue
            rows = ledger.accepted_by(
                att.hospital, since=att.at, resource=att.resource)
            for row in rows:
                gap = row.get("agreed_at", 0.0) - att.at
                if not (0 < gap <= window):
                    continue
                if row.get("request_id") == att.request_id:
                    continue
                found.append(AuditReferral(
                    attestation=att,
                    adjacent_agreement=row.get("agreement_id", ""),
                    minutes_after=gap,
                    false_positive_rate=false_positive_rate,
                    claim=(f"{att.hospital} certified under signature that no "
                           f"{att.resource.replace('_', ' ')} was available, and "
                           f"accepted one {gap:.1f} minutes later. Worth checking "
                           f"against its own records; a discharge in that window "
                           f"would explain it innocently."),
                ))
                break
        self.referrals = found
        return found

    def measure_false_positive_rate(self, ledger, now: float,
                                    window: float = AUDIT_REFERRAL_WINDOW_MINUTES
                                    ) -> float:
        """Referral rate over a population of attestations known to be true.

        Only meaningful on a run where no dishonest hospital signed -- i.e.
        deterrence at 1.0 -- where every referral is by construction a false
        positive. `experiments/attestation.py` calibrates it this way and quotes
        the result beside every referral count.
        """
        if not self._all:
            return 0.0
        return len(self.audit(ledger, now, window)) / len(self._all)

    def stats(self) -> dict[str, Any]:
        asked = self.issued + self.declined
        return {
            "backend": signer_backend(),
            "non_repudiation": provides_non_repudiation(),
            "attestations_issued": self.issued,
            "attestations_declined": self.declined,
            "declined_share": (self.declined / asked) if asked else 0.0,
            "verified": self.verified,
            "forged_rejected": self.forged_rejected,
            "audit_referrals": len(self.referrals),
        }
