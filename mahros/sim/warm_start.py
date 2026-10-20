"""Start a real network at the occupancy its hospitals reported, not empty.

Starting with every bed empty would flatter early transfers. A warm start
fills each bed pool
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
    cath-lab use; those start empty and are labelled assumed.
    """
    inpatient_rate = min(1.0, (used / beds)) if (used is not None and beds) else 0.70
    icu_cap = caps.get(ResourceType.ICU_BED, 0)
    out: dict[ResourceType, tuple[int, str, str]] = {}

    if icu_used is not None and icu_cap:
        icu_occ = min(icu_cap, max(0, round(icu_used)))
        out[ResourceType.ICU_BED] = (icu_occ, "observed", f"{HHS}: staffed adult ICU beds occupied")
    else:
        icu_occ = round(icu_cap * inpatient_rate)
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

    # OR slots, cath labs and ventilators: nothing public reports their use,
    # and procedure rooms turn over in hours, not days. They start empty, as
    # they do in the simulation, so the console and the experiments agree.
    # (Filling them at the bed occupancy rate showed 21 of 24 cath labs as
    # full, which is not what anyone observed.)
    for kind in (ResourceType.OR_SLOT, ResourceType.CATH_LAB, ResourceType.VENTILATOR):
        if caps.get(kind, 0):
            out[kind] = (0, "assumed",
                         "no public data on use; starts empty, as in the simulation")
    return out


def residual_stay(rng: random.Random, mean_los_param: float, los_scale: float) -> float:
    """Minutes left for a patient already in a bed (length-biased lognormal)."""
    mu = math.log(mean_los_param) + ADMISSION_SIGMA ** 2
    stay = rng.lognormvariate(mu, ADMISSION_SIGMA)
    return max(1.0, rng.random() * stay) * los_scale
