"""Which patients need moving, and for what: from real California transfers.

The escalation case-mix (what share of transfer requests need a cath lab, a
neuro ICU, a trauma theatre...) on real networks comes from 37,439 interhospital transfers recorded in California hospital
discharge data for 2021 (see experiments/fetch_transfer_mix.py).

Two steps are author decisions, stated here so they can be challenged:

1. **Diagnosis -> what the patient needs.** The data gives the diagnostic
   group (MDC) of each transfer, not the resource it needed. `MDC_TO_PROFILE`
   maps each MDC to one escalation profile. Circulatory transfers are split
   evenly between a cath lab and a cardiac ICU bed (`CARDIAC_CATH_SHARE`),
   because the data cannot say which.
2. **Children.** The data's own age breakdown gives the share of transfers
   aged 0-18; that share goes to the paediatric ICU profile and every other
   profile is scaled down to make room.

Acuity ranges and lengths of stay per profile remain model assumptions.

What the real mix changed (old tuned weight -> California 2021):
cardiac 9% -> ~25%, trauma 12% -> ~2.5%, paediatric 7% -> ~1.1%.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parent / "data" / "ca_transfer_mix_2021.json"

#: Share of circulatory-system transfers that need a cath lab rather than a
#: cardiac ICU bed. Not in the data; an even split, declared.
CARDIAC_CATH_SHARE = 0.5

#: Profile keys match `mahros.sim.scenario.PROFILE_KEYS`.
MDC_TO_PROFILE: dict[str, str] = {
    "Diseases & Disorders of the Circulatory System": "cardiac",       # split below
    "Infectious & Parasitic Diseases, Systemic or Unspecified Sites": "icu_general",
    "Diseases & Disorders of the Respiratory System": "ventilator",
    "Diseases & Disorders of the Nervous System": "icu_neuro",
    "Transplants and Tracheostomies (PRE-MDC)": "ventilator",
    "Diseases & Disorders of the Musculoskeletal System & Connective Tissue": "or_general",
    "Injuries, Poisonings & Toxic Effects of Drugs": "or_trauma",
    "Multiple Significant Trauma": "or_trauma",
    "Burns": "icu_burns",
    "Pregnancy, Childbirth & the Puerperium": "hdu_obstetric",
    # Newborns are counted through the age breakdown (children), not here.
    "Newborns & Other Neonates with Conditions Originating in Perinatal Period": None,
}
#: Every other diagnostic group: a medical step-down bed.
DEFAULT_PROFILE = "hdu_general"


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    return json.loads(DATA.read_text(encoding="utf-8"))


def observed_weights() -> dict[str, float]:
    """Profile key -> share of transfer requests, summing to 1."""
    data = load()
    counts: dict[str, float] = {}
    for mdc, row in data["by_mdc"].items():
        key = MDC_TO_PROFILE.get(mdc, DEFAULT_PROFILE)
        if key is None:
            continue
        n = float(row["transfers"])
        if key == "cardiac":
            counts["cath_cardiac"] = counts.get("cath_cardiac", 0.0) + n * CARDIAC_CATH_SHARE
            counts["icu_cardiac"] = counts.get("icu_cardiac", 0.0) + n * (1 - CARDIAC_CATH_SHARE)
        else:
            counts[key] = counts.get(key, 0.0) + n
    total = sum(counts.values())
    children = data["by_age_overall"]["0 to 18"] / data["transfers_total"]
    weights = {k: (v / total) * (1 - children) for k, v in counts.items()}
    weights["icu_paediatric"] = children
    return weights


def describe() -> str:
    data = load()
    w = observed_weights()
    lines = [f"{data['source']['publisher']}, {data['source']['coverage']}: "
             f"{data['transfers_total']:,} transfers "
             f"({data['transfer_rate_of_discharges']:.1%} of discharges)",
             "", f"{'profile':<16}{'share':>8}"]
    lines += [f"{k:<16}{v:>8.1%}" for k, v in sorted(w.items(), key=lambda kv: -kv[1])]
    return "\n".join(lines)
