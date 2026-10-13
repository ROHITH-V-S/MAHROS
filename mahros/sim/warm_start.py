"""Start a real network at the occupancy its hospitals reported, not empty.

Every run used to begin with every bed empty, which flatters early transfers
(the README listed it as a known limitation). A warm start fills each bed pool
to what the hospital reported to HHS in the scenario's week, and gives each of
those patients a realistic *remaining* stay.

Remaining stay, stated precisely. The patients found in a bed at a random
moment are not a random sample of stays: long stays are more likely to be in
progress (length-biased sampling). For a lognormal(mu, sigma) stay the
length-biased stay is lognormal(mu + sigma^2, sigma), and the time left is a
uniform fraction of it. So the warm-started network starts close to the
steady state the arrival rates were fitted to, rather than draining early.

The same occupancy rule produces the live console's default data
(`mahros.db.defaults`), so the console and the experiments start from the
same place.
"""

from __future__ import annotations

import math
import random

from ..core.types import ResourceType

HHS = "HHS/CDC NHSN facility report"

#: Pools that the simulation admits patients to, and so can warm-start. OR
#: slots, ventilators and cath labs are used only by escalations, and nothing
#: public reports their use, so they start empty in the simulation.
WARM_POOLS = (ResourceType.WARD_BED, ResourceType.HDU_BED, ResourceType.ICU_BED)

#: Must match the generator's lognormal spread for admissions.
ADMISSION_SIGMA = 0.6


def default_occupancy(caps: dict[ResourceType, int], used: float | None,
                      icu_used: float | None, beds: float,
                      ) -> dict[ResourceType, tuple[int, str, str]]:
    """Occupancy per pool in the reference week: (occupied, provenance, source).

    ICU is reported directly. Ward and step-down share what is left of the
    inpatient count, split by size. Nothing public reports OR, ventilator or
    cath-lab use, so those follow the hospital's reported occupancy rate and
    are labelled assumed.
    """
    inpatient_rate = min(1.0, (used / beds)) if (used is not None and beds) else 0.70
    icu_cap = caps.get(ResourceType.ICU_BED, 0)
    out: dict[ResourceType, tuple[int, str, str]] = {}

    if icu_used is not None and icu_cap:
        icu_occ = min(icu_cap, max(0, round(icu_used)))
        icu_rate = icu_occ / icu_cap
        out[ResourceType.ICU_BED] = (icu_occ, "observed", f"{HHS}: staffed adult ICU beds occupied")
    else:
        icu_occ = round(icu_cap * inpatient_rate)
        icu_rate = inpatient_rate
        out[ResourceType.ICU_BED] = (icu_occ, "derived",
                                     f"ICU not reported; {HHS} inpatient occupancy rate applied")

    ward_cap = caps.get(ResourceType.WARD_BED, 0)
    hdu_cap = caps.get(ResourceType.HDU_BED, 0)
    if used is not None:
        rest = max(0.0, used - (icu_used if icu_used is not None else icu_occ))
        share = ward_cap / (ward_cap + hdu_cap) if (ward_cap + hdu_cap) else 1.0
        ward_occ = min(ward_cap, round(rest * share))
        hdu_occ = min(hdu_cap, round(rest * (1 - share)))
        src = f"{HHS}: inpatient beds occupied minus ICU, split by pool size"
        out[ResourceType.WARD_BED] = (ward_occ, "derived", src)
        if hdu_cap:
            out[ResourceType.HDU_BED] = (hdu_occ, "derived", src)
    else:
        out[ResourceType.WARD_BED] = (round(ward_cap * 0.70), "assumed",
                                      "occupancy not reported; network mean 0.70")
        if hdu_cap:
            out[ResourceType.HDU_BED] = (round(hdu_cap * 0.70), "assumed",
                                         "occupancy not reported; network mean 0.70")

    for kind, rate, label in (
        (ResourceType.OR_SLOT, inpatient_rate, "inpatient"),
        (ResourceType.CATH_LAB, inpatient_rate, "inpatient"),
        (ResourceType.VENTILATOR, icu_rate, "ICU"),
    ):
        cap = caps.get(kind, 0)
        if cap:
            out[kind] = (min(cap, round(cap * rate)), "assumed",
                         f"no public data on use; set to the hospital's {label} occupancy rate")
    return out


def residual_stay(rng: random.Random, mean_los_param: float, los_scale: float) -> float:
    """Minutes left for a patient already in a bed (length-biased lognormal)."""
    mu = math.log(mean_los_param) + ADMISSION_SIGMA ** 2
    stay = rng.lognormvariate(mu, ADMISSION_SIGMA)
    return max(1.0, rng.random() * stay) * los_scale
