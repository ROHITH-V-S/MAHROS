"""Layer 3: the LLM coordinator.

Scope is deliberately narrow, and you should defend it exactly this way:

  * The coordinator is invoked **only on contested decisions** -- cases where
    the deterministic scorer cannot separate the top candidates, or where no
    feasible bid exists and someone must reason about the least-bad option.
    That keeps token cost near zero and keeps the LLM out of the hot path.

  * In the default configuration it **explains but does not decide**
    (`CNPConfig.enable_llm_arbitration = False`). The deterministic policy picks
    the winner; the LLM produces the plain-English rationale that goes on the
    ledger. This is what makes the whole evaluation reproducible.

  * Turning arbitration on is an **ablation**, not the main system. Report both
    arms: "LLM-as-explainer" vs "LLM-as-arbiter". Showing that arbitration does
    *not* beat the deterministic rule is a perfectly good, honest result -- and
    a far stronger paper than pretending the LLM is doing the optimisation.

Every LLM output is schema-validated and falls back to a template on any
failure, so the simulation can never hang or crash on a bad generation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..core.types import TransferRequest
from ..fairness.metrics import FairnessLedger
from ..negotiation.scoring import ScoredBid
from .client import LLMClient

SYSTEM_PROMPT = """You are the coordination agent for a hospital transfer network.
A patient is already admitted and stabilised at one hospital and now needs a level
of care that hospital cannot provide right now. Peer hospitals have submitted bids.

Your job is to explain the choice between closely-matched options in clear,
non-technical English that a bed manager or clinician can act on and audit later.

Rules you must never break:
1. Clinical safety first. A patient must reach definitive care inside the stated
   safe window. Never recommend an option that misses it.
2. Never trade a critically ill patient's minutes for load balancing.
3. Load balancing is a tie-break only, when options are clinically comparable.
4. Never speculate about patient identity, demographics, or ability to pay.
   You are not given that information and must not ask for it.

Respond with ONLY a JSON object, no markdown fences:
{"winner": "<hospital_id>", "rationale": "<2-3 sentences, plain English>",
 "risk": "<one clinical risk of this choice>", "confidence": <0.0-1.0>}"""


@dataclass
class CoordinatorDecision:
    winner: str | None = None
    rationale: str = ""
    risk: str = ""
    confidence: float = 0.0
    source: str = "template"             # llm | template | fallback
    raw: str = ""


class LLMCoordinator:
    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or LLMClient()
        self.invocations = 0
        self.llm_successes = 0
        self.schema_failures = 0
        self.guardrail_blocks = 0
        self.log: list[dict] = []

    # -- public API -------------------------------------------------------- #
    def arbitrate(
        self,
        req: TransferRequest,
        scored: list[ScoredBid],
        fairness: FairnessLedger,
        now: float,
    ) -> CoordinatorDecision:
        self.invocations += 1
        if not scored:
            return CoordinatorDecision(rationale="No feasible peer met the clinical window.")

        if not self.client.available:
            return self._template(req, scored)

        prompt = self._build_prompt(req, scored, fairness, now)
        resp = self.client.complete(SYSTEM_PROMPT, prompt)
        if not resp.ok or not resp.text.strip():
            return self._template(req, scored)

        decision = self._parse(resp.text)
        if decision is None:
            self.schema_failures += 1
            return self._template(req, scored)

        # Guardrail: the LLM may only choose among options that already passed
        # the hard clinical constraint. Anything else is discarded.
        valid = {s.bid.bidder for s in scored}
        if decision.winner not in valid:
            self.guardrail_blocks += 1
            fallback = self._template(req, scored)
            fallback.rationale = decision.rationale or fallback.rationale
            return fallback

        self.llm_successes += 1
        decision.source = "llm"
        decision.raw = resp.text
        self.log.append(
            {
                "request_id": req.request_id,
                "winner": decision.winner,
                "confidence": decision.confidence,
                "candidates": [s.bid.bidder for s in scored[:4]],
            }
        )
        return decision

    def explain(self, req: TransferRequest, chosen: ScoredBid, scored: list[ScoredBid]) -> str:
        """Narrative explanation for an already-made decision (the default path)."""
        d = self.arbitrate(req, scored, FairnessLedger([]), req.awarded_at or 0.0)
        return d.rationale

    # -- prompt ------------------------------------------------------------ #
    @staticmethod
    def _build_prompt(
        req: TransferRequest, scored: list[ScoredBid], fairness: FairnessLedger, now: float
    ) -> str:
        lines = [
            "PATIENT NEED (no identifying information is available to you):",
            f"  resource needed: {req.resource.value.replace('_', ' ')}",
            f"  specialty:       {req.specialty.value}",
            f"  urgency:         {req.acuity.name} (level {int(req.acuity)}/5)",
            f"  safe window:     {req.acuity.safe_window_minutes} minutes from escalation",
            f"  minutes elapsed: {now - req.created_at:.0f}",
            "",
            "CANDIDATE HOSPITALS (all already verified able to meet the window):",
        ]
        for i, s in enumerate(scored[:5], 1):
            credit = fairness.credit(s.bid.bidder) if fairness.hospitals else 0.0
            burden = ("above" if credit > 0.15 else "below" if credit < -0.15 else "at")
            lines.append(
                f"  {i}. {s.bid.bidder}: arrives in {s.bid.time_to_care:.0f} min "
                f"({s.slack_minutes:.0f} min of window to spare), "
                f"specialty match {s.capability_term:.0%}, "
                f"will be {s.bid.post_accept_strain:.0%} full after accepting, "
                f"recently carrying {burden} its fair share of transfers."
            )
        lines += [
            "",
            "The deterministic scorer ranked these near-identically, so this is a "
            "judgement call. Choose one and explain it.",
        ]
        return "\n".join(lines)

    # -- parsing ----------------------------------------------------------- #
    @staticmethod
    def _parse(text: str) -> CoordinatorDecision | None:
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.MULTILINE).strip()
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict) or "winner" not in data:
            return None
        return CoordinatorDecision(
            winner=str(data.get("winner", "")).strip(),
            rationale=str(data.get("rationale", "")).strip(),
            risk=str(data.get("risk", "")).strip(),
            confidence=float(data.get("confidence", 0.5) or 0.5),
        )

    # -- deterministic fallback -------------------------------------------- #
    @staticmethod
    def _template(req: TransferRequest, scored: list[ScoredBid]) -> CoordinatorDecision:
        top = scored[0]
        rationale = (
            f"{top.bid.bidder} was selected for this {req.acuity.name.lower()} "
            f"{req.resource.value.replace('_', ' ')} request. It can begin care in "
            f"{top.bid.time_to_care:.0f} minutes, which leaves {top.slack_minutes:.0f} "
            f"minutes of the {req.acuity.safe_window_minutes}-minute clinical window, "
            f"and it matches the required {req.specialty.value} capability at "
            f"{top.capability_term:.0%}."
        )
        if len(scored) > 1:
            alt = scored[1]
            rationale += (
                f" The closest alternative, {alt.bid.bidder}, would have taken "
                f"{alt.bid.time_to_care - top.bid.time_to_care:+.0f} minutes longer."
            )
        risk = (
            "Tight clinical window; delay in transport would breach it."
            if top.slack_minutes < 30
            else "Receiving unit will be near capacity after this admission."
            if top.bid.post_accept_strain > 0.85
            else "No elevated risk identified for this option."
        )
        return CoordinatorDecision(
            winner=top.bid.bidder,
            rationale=rationale,
            risk=risk,
            confidence=0.7,
            source="template",
        )

    def stats(self) -> dict:
        return {
            "invocations": self.invocations,
            "llm_successes": self.llm_successes,
            "schema_failures": self.schema_failures,
            "guardrail_blocks": self.guardrail_blocks,
            **self.client.stats(),
        }
