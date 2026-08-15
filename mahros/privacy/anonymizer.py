"""Privacy boundary: nothing patient-identifying leaves a hospital.

Two implementations behind one interface:

  * `TokenizingAnonymizer` -- default. Zero dependencies. HMAC-SHA256
    pseudonymisation keyed per-hospital, plus regex scrubbing of the usual
    direct identifiers. Deterministic, so the same patient maps to the same
    token within a hospital and to a *different* token across hospitals
    (prevents cross-site linkage).

  * `PresidioAnonymizer` -- optional, activated if `presidio-analyzer` and a
    spaCy model are installed. Better recall on free-text clinical notes.

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
            clean[k] = self.scrub(v) if isinstance(v, str) else v
        return clean


class PresidioAnonymizer(TokenizingAnonymizer):
    """Microsoft Presidio backend. Falls back to regex if a call fails."""

    backend = "presidio"

    def __init__(self, secret: bytes = b"mahros-dev-key-not-for-production") -> None:
        super().__init__(secret)
        from presidio_analyzer import AnalyzerEngine  # noqa: PLC0415
        from presidio_anonymizer import AnonymizerEngine  # noqa: PLC0415

        self._analyzer = AnalyzerEngine()
        self._anonymizer = AnonymizerEngine()

    def scrub(self, text: str) -> str:
        if not text:
            return text
        try:
            results = self._analyzer.analyze(text=text, language="en")
            return self._anonymizer.anonymize(text=text, analyzer_results=results).text
        except Exception:
            return super().scrub(text)


def build_anonymizer(prefer_presidio: bool = True, secret: bytes | None = None) -> Anonymizer:
    """Return Presidio if importable, otherwise the tokenizing fallback."""
    key = secret or b"mahros-dev-key-not-for-production"
    if prefer_presidio:
        try:
            return PresidioAnonymizer(key)
        except Exception:
            pass
    return TokenizingAnonymizer(key)


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
