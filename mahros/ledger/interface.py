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
            "terms_hash": terms_hash,
            "origin": agreement.origin,
            "receiver": agreement.receiver,
            "resource": agreement.resource.value,
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
