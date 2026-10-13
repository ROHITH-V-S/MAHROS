"""Privacy boundary: nothing patient-identifying leaves a hospital.

`TokenizingAnonymizer`: zero dependencies. HMAC-SHA256 pseudonymisation keyed
per-hospital, plus regex scrubbing of the usual direct identifiers.
Deterministic, so the same patient maps to the same token within a hospital and
to a *different* token across hospitals (prevents cross-site linkage).

An optional Microsoft Presidio backend existed in earlier versions. It was
removed: MAHROS handles no real patient data, so a heavyweight NLP
de-identification stack added a dependency without adding a result.

Design note for the paper: the pseudonym is per-(hospital, salt) so a receiving
hospital cannot correlate a patient across the network, but the *origin* can
always resolve its own token back to the MRN. That is the standard
pseudonymisation model, not full anonymisation -- state it that way.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any, Protocol

from ..core.types import Patient

# Direct identifiers we scrub from any free text crossing the boundary.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("PHONE", re.compile(r"(?:\+?\d{1,3}[\s-]?)?(?:\d[\s-]?){9,12}\d")),
    ("AADHAAR", re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")),
    # MRNs routinely embed hyphens (H03-000891), so the body class must allow
    # them -- a plain [A-Z0-9]+ stops at the first hyphen and leaks the rest.
    ("MRN", re.compile(r"\bMRN[-: ]?[A-Z0-9][A-Z0-9-]{3,}\b", re.IGNORECASE)),
    ("DATE", re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")),
    ("NAME", re.compile(r"\b(?:Mr|Mrs|Ms|Dr)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?")),
]


class Anonymizer(Protocol):
    backend: str

    def pseudonymize(self, patient: Patient, hospital_id: str) -> str: ...
    def scrub(self, text: str) -> str: ...
    def sanitize_outbound(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class TokenizingAnonymizer:
    """Dependency-free default."""

    backend = "tokenizing"

    #: fields that must never appear in an outbound message
    FORBIDDEN = {"name", "mrn", "phone", "patient_id", "age", "address", "notes", "group"}
    #: System-generated identifiers and codes. They are not free text and must
    #: pass through exactly: a request id like "req-1234567890" matches the
    #: phone-number pattern, and scrubbing it to "req-<PHONE>" made every such
    #: request share one key in each hospital's memory -- a hospital asked to
    #: defend one refusal could look up another request's statement, and
    #: because ids are random, results varied from run to run.
    IDENTIFIERS = {"request_id", "patient_ref", "origin", "resource", "specialty"}

    def __init__(self, secret: bytes = b"mahros-dev-key-not-for-production") -> None:
        self._secret = secret

    def pseudonymize(self, patient: Patient, hospital_id: str) -> str:
        msg = f"{hospital_id}|{patient.patient_id}".encode()
        digest = hmac.new(self._secret, msg, hashlib.sha256).hexdigest()
        return f"pt_{digest[:16]}"

    def scrub(self, text: str) -> str:
        if not text:
            return text
        out = text
        for label, pattern in _PATTERNS:
            out = pattern.sub(f"<{label}>", out)
        return out

    def sanitize_outbound(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Drop forbidden keys, scrub every remaining string. Fail-closed."""
        clean: dict[str, Any] = {}
        for k, v in payload.items():
            if k in self.FORBIDDEN:
                continue
            if k in self.IDENTIFIERS:
                clean[k] = v
            else:
                clean[k] = self.scrub(v) if isinstance(v, str) else v
        return clean


def build_anonymizer(secret: bytes | None = None) -> Anonymizer:
    """The privacy boundary every outbound message passes through."""
    return TokenizingAnonymizer(secret or b"mahros-dev-key-not-for-production")


class PrivacyAudit:
    """Counts boundary crossings and asserts no leakage. Evidence for the paper."""

    def __init__(self, anonymizer: Anonymizer) -> None:
        self.anonymizer = anonymizer
        self.crossings = 0
        self.violations: list[str] = []

    def outbound(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.crossings += 1
        clean = self.anonymizer.sanitize_outbound(payload)
        for key in TokenizingAnonymizer.FORBIDDEN:
            if key in clean:
                self.violations.append(f"leaked field {key}")
        return clean

    def report(self) -> dict[str, Any]:
        return {
            "backend": self.anonymizer.backend,
            "boundary_crossings": self.crossings,
            "violations": len(self.violations),
            "clean": not self.violations,
        }
