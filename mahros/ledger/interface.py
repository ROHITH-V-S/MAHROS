"""Layer 5: tamper-evident audit ledger.

Framing note for the paper -- be precise here, reviewers will be:

MAHROS does not need, and should not claim, a public permissionless blockchain.
The trust model is a **permissioned consortium ledger** among hospitals in one
network/trust/health system. What we actually require is: append-only,
tamper-evident, independently verifiable by every participant, and no single
party able to rewrite history unilaterally.

`HashChainLedger` (default) provides append-only + tamper-evidence + independent
verification with zero dependencies and no gas cost -- exactly the properties
the fairness argument needs. `EvmLedger` adds multi-party consensus on a local
Hardhat/Anvil chain, i.e. removes the trust in whoever hosts the hash chain.
Report both; the hash chain is what the experiments run on, the EVM backend is
the deployment story.
"""

from __future__ import annotations

import abc
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from ..core.types import Agreement


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


@dataclass
class Receipt:
    """Proof that an agreement was recorded."""

    ok: bool
    terms_hash: str
    block_index: int = -1
    block_hash: str = ""
    backend: str = "hashchain"
    tx_hash: str = ""
    latency_ms: float = 0.0


@dataclass
class Block:
    index: int
    timestamp: float
    prev_hash: str
    entries: list[dict[str, Any]] = field(default_factory=list)
    merkle_root: str = ""
    hash: str = ""

    def compute_merkle_root(self) -> str:
        leaves = [sha256_hex(json.dumps(e, sort_keys=True, separators=(",", ":")))
                  for e in self.entries]
        if not leaves:
            return sha256_hex("")
        while len(leaves) > 1:
            if len(leaves) % 2:
                leaves.append(leaves[-1])
            leaves = [sha256_hex(leaves[i] + leaves[i + 1])
                      for i in range(0, len(leaves), 2)]
        return leaves[0]

    def compute_hash(self) -> str:
        header = json.dumps(
            {
                "index": self.index,
                "timestamp": round(self.timestamp, 6),
                "prev_hash": self.prev_hash,
                "merkle_root": self.merkle_root,
                "n": len(self.entries),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256_hex(header)

    def seal(self) -> None:
        self.merkle_root = self.compute_merkle_root()
        self.hash = self.compute_hash()


class LedgerBackend(abc.ABC):
    name: str = "abstract"

    @abc.abstractmethod
    def record_agreement(self, agreement: Agreement) -> Receipt: ...

    @abc.abstractmethod
    def verify(self) -> dict[str, Any]: ...

    @abc.abstractmethod
    def export(self, path: str) -> None: ...

    @abc.abstractmethod
    def stats(self) -> dict[str, Any]: ...


class HashChainLedger(LedgerBackend):
    """Append-only Merkle-linked chain. Default backend."""

    name = "hashchain"

    def __init__(self, block_size: int = 16) -> None:
        self.block_size = block_size
        self.chain: list[Block] = []
        self._pending: list[dict[str, Any]] = []
        self._record_count = 0
        self._latencies: list[float] = []
        genesis = Block(index=0, timestamp=0.0, prev_hash="0" * 64)
        genesis.seal()
        self.chain.append(genesis)

    # -- writing ----------------------------------------------------------- #
    def record_agreement(self, agreement: Agreement) -> Receipt:
        t0 = time.perf_counter()
        canonical = agreement.canonical()
        terms_hash = sha256_hex(canonical)
        entry = {
            "agreement_id": agreement.agreement_id,
            "request_id": agreement.request_id,
            "terms_hash": terms_hash,
            "origin": agreement.origin,
            "receiver": agreement.receiver,
            "resource": agreement.resource.value,
            # The service agreed to. Needed so a later capability refusal can be
            # checked against what this hospital has already taken on.
            "specialty": agreement.specialty.value,
            "patient_ref": agreement.patient_ref,   # pseudonymous, safe on-ledger
            "agreed_at": round(agreement.agreed_at, 4),
            "decided_by": agreement.decided_by,
        }
        self._pending.append(entry)
        self._record_count += 1

        block_index, block_hash = -1, ""
        if len(self._pending) >= self.block_size:
            block = self._seal_pending()
            block_index, block_hash = block.index, block.hash

        latency = (time.perf_counter() - t0) * 1000.0
        self._latencies.append(latency)
        return Receipt(
            ok=True,
            terms_hash=terms_hash,
            block_index=block_index,
            block_hash=block_hash,
            backend=self.name,
            latency_ms=latency,
        )

    def record_refusal(
        self,
        hospital: str,
        resource: str,
        request_id: str,
        reason: str,
        at: float,
    ) -> None:
        """Record that a hospital declined, and why.

        This is what turns a refusal from a free action into a **commitment**.
        Saying "we are down to our last ICU bed" is cheap only if nobody
        remembers you said it. Once it is on the shared record, accepting a
        different ICU patient twenty minutes later is a contradiction anyone
        can point to -- without anybody having disclosed a bed count.

        Only capacity claims are recorded. Capability claims are checked
        against the public service directory and need no history.
        """
        self._pending.append({
            "kind": "refusal",
            "hospital": hospital,
            "resource": resource,
            "request_id": request_id,
            "reason": reason,
            "agreed_at": round(at, 4),
        })
        self._record_count += 1
        if len(self._pending) >= self.block_size:
            self._seal_pending()

    def refusals_by(
        self, hospital: str, since: float | None = None, resource: str | None = None
    ) -> list[dict[str, Any]]:
        rows = [
            e for e in self.history()
            if e.get("kind") == "refusal"
            and e.get("hospital") == hospital
            and (since is None or e.get("agreed_at", 0.0) >= since)
            and (resource is None or e.get("resource") == resource)
        ]
        rows.sort(key=lambda e: e.get("agreed_at", 0.0), reverse=True)
        return rows

    def broken_commitments(
        self, hospital: str, window_minutes: float = 60.0, since: float | None = None
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """Refusals this hospital contradicted by its own later acceptances.

        Returns (refusal, acceptance) pairs where the hospital declined a
        resource and then accepted a different patient needing that same
        resource inside `window_minutes`. This is the cleanest possible
        evidence of bad faith: both halves are the hospital's own statements,
        neither reveals private state, and no third party had to be trusted.
        """
        refusals = self.refusals_by(hospital, since=since)
        accepts = [e for e in self.history()
                   if e.get("kind") != "refusal" and e.get("receiver") == hospital]
        pairs = []
        for refusal in refusals:
            t0 = refusal.get("agreed_at", 0.0)
            for accept in accepts:
                t1 = accept.get("agreed_at", 0.0)
                if (accept.get("resource") == refusal.get("resource")
                        and t0 < t1 <= t0 + window_minutes
                        and accept.get("request_id") != refusal.get("request_id")):
                    pairs.append((refusal, accept))
                    break
        return pairs

    def _seal_pending(self) -> Block:
        block = Block(
            index=len(self.chain),
            timestamp=time.time(),
            prev_hash=self.chain[-1].hash,
            entries=list(self._pending),
        )
        block.seal()
        self.chain.append(block)
        self._pending.clear()
        return block

    def flush(self) -> None:
        if self._pending:
            self._seal_pending()

    # -- verification ------------------------------------------------------ #
    def verify(self) -> dict[str, Any]:
        """Independent re-derivation of every link. Any edit breaks this."""
        self.flush()
        errors: list[str] = []
        for i, block in enumerate(self.chain):
            if block.compute_merkle_root() != block.merkle_root:
                errors.append(f"block {i}: merkle root mismatch")
            if block.compute_hash() != block.hash:
                errors.append(f"block {i}: header hash mismatch")
            if i > 0 and block.prev_hash != self.chain[i - 1].hash:
                errors.append(f"block {i}: broken link to {i - 1}")
        return {
            "valid": not errors,
            "blocks": len(self.chain),
            "records": self._record_count,
            "errors": errors,
        }

    def contains(self, agreement: Agreement) -> bool:
        target = sha256_hex(agreement.canonical())
        return any(e["terms_hash"] == target for b in self.chain for e in b.entries)

    # -- history: the evidence base for challenging a refusal -------------- #
    def history(self) -> list[dict[str, Any]]:
        """Every recorded agreement, sealed or still pending, oldest first.

        Pending entries are included deliberately. A hospital that accepted a
        cardiac patient four minutes ago and is now claiming it has no cardiac
        capability must be checkable *now*, not after the next block seals.
        Tamper-evidence is a property of the sealed chain; queryability has to
        cover the whole record or the challenge mechanism has a blind spot
        exactly the width of one block.
        """
        out: list[dict[str, Any]] = []
        for block in self.chain:
            out.extend(block.entries)
        out.extend(self._pending)
        return out

    def accepted_by(
        self,
        hospital: str,
        since: float | None = None,
        resource: str | None = None,
        specialty: str | None = None,
    ) -> list[dict[str, Any]]:
        """Agreements where `hospital` was the *receiver*, newest first.

        This is the read side of the audit ledger, and the reason the ledger is
        load-bearing rather than decorative: it is what a challenge is made of.
        """
        rows = [
            e for e in self.history()
            if e.get("receiver") == hospital
            and (since is None or e.get("agreed_at", 0.0) >= since)
            and (resource is None or e.get("resource") == resource)
            and (specialty is None or e.get("specialty") == specialty)
        ]
        rows.sort(key=lambda e: e.get("agreed_at", 0.0), reverse=True)
        return rows

    # -- io ---------------------------------------------------------------- #
    def export(self, path: str) -> None:
        self.flush()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump([asdict(b) for b in self.chain], fh, indent=2)

    def stats(self) -> dict[str, Any]:
        self.flush()
        lat = self._latencies
        return {
            "backend": self.name,
            "records": self._record_count,
            "blocks": len(self.chain),
            "mean_write_latency_ms": round(sum(lat) / len(lat), 4) if lat else 0.0,
            "head": self.chain[-1].hash[:16] if self.chain else "",
        }


class NullLedger(LedgerBackend):
    """No-audit control condition for the ablation study."""

    name = "null"

    def record_agreement(self, agreement: Agreement) -> Receipt:
        return Receipt(ok=True, terms_hash="", backend=self.name)

    def verify(self) -> dict[str, Any]:
        return {"valid": False, "blocks": 0, "records": 0,
                "errors": ["no audit trail kept"]}

    def export(self, path: str) -> None:
        pass

    def stats(self) -> dict[str, Any]:
        return {"backend": self.name, "records": 0}

    # A network with no audit trail has no evidence to argue from. Returning an
    # empty history is not a stub -- it is the correct behaviour, and it is what
    # makes the no-ledger ablation a *real* ablation: without the ledger, every
    # challenge fails for want of evidence and strategic refusal goes unpunished.
    def history(self) -> list[dict[str, Any]]:
        return []

    def accepted_by(self, hospital: str, since: float | None = None,
                    resource: str | None = None,
                    specialty: str | None = None) -> list[dict[str, Any]]:
        return []

    def record_refusal(self, hospital: str, resource: str, request_id: str,
                       reason: str, at: float) -> None:
        return None

    def refusals_by(self, hospital: str, since: float | None = None,
                    resource: str | None = None) -> list[dict[str, Any]]:
        return []

    def broken_commitments(self, hospital: str, window_minutes: float = 60.0,
                           since: float | None = None) -> list:
        return []
