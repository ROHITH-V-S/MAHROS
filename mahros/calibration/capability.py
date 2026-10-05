"""Which hospital can actually treat what -- observed, not assumed.

The gap this closes
-------------------
Capability is the reason escalation transfer exists at all. A patient is moved
because the hospital holding them cannot provide the care they now need, so
"which hospital runs a cath lab" is not a detail of the model -- it decides who
can bid.

Until now it was the largest *assumed* block in the whole system. The HHS
capacity dataset reports beds, ICU beds and occupancy, and no specialty except
paediatric intensive care, so every other capability was generated from the
hospital's tier: tier 3 got cardiac, neuro, trauma and obstetrics, tier 1 got
none. That is a guess dressed as a fact, and it was visibly wrong in places --
it made a large obstetric hospital a cardiac centre because it had enough beds.

Source
------
**CMS Provider of Services (POS) file**, Q1 2021, via the NBER public mirror at
``https://data.nber.org/pos/``. The primary host, ``data.cms.gov``, refuses
automated requests; NBER mirrors the same federal files and is reachable.

The POS file is the facility-certification record every Medicare-participating
provider is described by. It carries ``PRVDR_NUM`` -- the CCN -- which is the
same identifier the HHS capacity records use, so the join is exact rather than
fuzzy name-matching. **All 27 Houston facilities matched.**

Service codes, from the official layout (``POS_OTHER_LAYOUT_DEC19.txt``):

    0 = NOT PROVIDED
    1 = PROVIDED BY STAFF
    2 = PROVIDED UNDER ARRANGEMENT
    3 = PROVIDED BY STAFF AND UNDER ARRANGEMENT

So any non-zero value means the service exists at that facility. The model
treats 1 and 3 as on-site capability and 2 as available-under-arrangement,
because a service provided only under arrangement is not something an
escalating patient can be sent to at short notice.

What this makes observed
------------------------
Cath labs (with a real *room count*), cardiac surgery, neurosurgery, trauma,
obstetrics, paediatrics and burns all stop being tier guesses.

What is still assumed, and stays that way
-----------------------------------------
* **Ventilators.** ``VNTLTR_BED_CNT`` exists in the POS file but is populated
  for **0 of 27** hospitals -- it is a long-term-care field. Ventilator counts
  remain a declared ratio.
* **Operating-room slots.** ``OPRTG_ROOM_SRVC_CD`` says a hospital has an
  operating room; it does not say how many. The count remains a declared ratio.
* **Trauma *level*.** The POS file records that shock-trauma services exist,
  not whether the facility is a Level I or Level III centre. That distinction
  matters clinically and is not available here.
"""

from __future__ import annotations

import csv
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"

#: NBER's mirror of the CMS Provider of Services files.
POS_BASE = "https://data.nber.org/pos/2022/orig"

#: Quarter used for the Houston network. Q1 2021 contains the 2021-01-10
#: reference week the capacity records use, so capability and capacity describe
#: the same hospitals at the same moment.
DEFAULT_QUARTER = "Q12021"

#: Service-code values that mean the service exists on site. 2 (under
#: arrangement only) is deliberately excluded from on-site capability: a service
#: you have to arrange elsewhere is not one an escalating patient can be sent to
#: within a 90-minute window.
ONSITE_CODES = {"1", "3"}
ANY_PROVISION_CODES = {"1", "2", "3"}

#: POS columns pulled. Keeping this list short keeps the vendored file small and
#: makes the provenance table exact.
FIELDS = [
    "PRVDR_NUM", "FAC_NAME", "STATE_CD", "BED_CNT", "CRTFD_BED_CNT",
    # capability
    "CRDC_CTHRTZTN_LAB_SRVC_CD", "CRDC_CTHRTZTN_PRCDR_ROOMS_CNT",
    "OPEN_HRT_SRGRY_SRVC_CD", "NRSRGCL_SRVC_CD", "SHCK_TRMA_SRVC_CD",
    "OB_SRVC_CD", "PED_SRVC_CD", "PED_ICU_SRVC_CD", "NEONTL_ICU_SRVC_CD",
    "BURN_CARE_UNIT_SRVC_CD", "ICU_SRVC_CD", "SRGCL_ICU_SRVC_CD",
    "CRNRY_CARE_UNIT_SRVC_CD", "OPRTG_ROOM_SRVC_CD", "DCTD_ER_SRVC_CD",
    # present in the file but unusable for hospitals; carried so the claim
    # "not available" can be checked rather than taken on trust
    "VNTLTR_BED_CNT",
]


def pos_url(quarter: str = DEFAULT_QUARTER) -> str:
    return f"{POS_BASE}/pos_other_{quarter}.csv"


def capability_path(metro: str) -> Path:
    return DATA_DIR / f"us_{metro}_metro_capability.json"


def _num(raw: str | None) -> int | None:
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def extract(csv_path: Path, ccns: set[str]) -> dict[str, dict[str, Any]]:
    """Pull the wanted CCNs out of a POS CSV.

    The file is ~109 MB with 473 columns, so it is streamed and only the
    declared `FIELDS` are kept.
    """
    csv.field_size_limit(10 ** 7)
    out: dict[str, dict[str, Any]] = {}
    with csv_path.open(newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            ccn = (row.get("PRVDR_NUM") or "").strip()
            if ccn in ccns:
                out[ccn] = {k: (row.get(k) or "").strip() for k in FIELDS}
    return out


def download(quarter: str, dest: Path) -> Path:
    """Fetch the POS quarter file. ~109 MB; only needed to re-vendor."""
    req = urllib.request.Request(
        pos_url(quarter),
        headers={"User-Agent": "MAHROS research (facility capability join)"})
    with urllib.request.urlopen(req, timeout=600) as resp, dest.open("wb") as fh:
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
    return dest


def build_payload(metro: str, quarter: str,
                  rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "source": {
            "dataset": "CMS Provider of Services (POS), Other Facilities",
            "quarter": quarter,
            "publisher": "Centers for Medicare & Medicaid Services",
            "mirror": POS_BASE,
            "mirror_note": "data.cms.gov refuses automated requests; NBER "
                           "mirrors the same federal files.",
            "join_key": "PRVDR_NUM (CCN) -- exact match against the HHS "
                        "capacity records, not name matching",
            "service_codes": {
                "0": "NOT PROVIDED", "1": "PROVIDED BY STAFF",
                "2": "PROVIDED UNDER ARRANGEMENT",
                "3": "PROVIDED BY STAFF AND UNDER ARRANGEMENT",
            },
            "onsite_codes": sorted(ONSITE_CODES),
            "onsite_note": "Code 2 is excluded from on-site capability: a "
                           "service only available under arrangement is not "
                           "somewhere an escalating patient can be sent within "
                           "the clinical window.",
            "not_available": {
                "VNTLTR_BED_CNT": "present in the layout but populated for 0 "
                                  "of 27 hospitals; a long-term-care field. "
                                  "Ventilator counts remain assumed.",
                "operating_room_count": "OPRTG_ROOM_SRVC_CD records that an "
                                        "operating room exists, not how many. "
                                        "OR slot counts remain assumed.",
                "trauma_level": "Shock-trauma service is recorded; Level I vs "
                                "Level III is not.",
            },
        },
        "metro": metro,
        "n_facilities": len(rows),
        "facilities": rows,
    }


def load_capability(metro: str = "houston") -> dict[str, Any] | None:
    """Vendored capability records, or None if they have not been built.

    Returns None rather than raising so a network without them still runs on
    the tier-based fallback, with the substitution reported.
    """
    path = capability_path(metro)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def onsite(row: dict[str, Any], field: str) -> bool:
    return (row.get(field) or "").strip() in ONSITE_CODES


def available(row: dict[str, Any], field: str) -> bool:
    return (row.get(field) or "").strip() in ANY_PROVISION_CODES


def cath_lab_rooms(row: dict[str, Any]) -> int:
    """Observed cath-lab room count, floored at 1 when the service exists.

    A hospital reporting a cath lab service but zero rooms is a reporting gap,
    not a hospital doing angioplasty in a corridor.
    """
    rooms = _num(row.get("CRDC_CTHRTZTN_PRCDR_ROOMS_CNT")) or 0
    if rooms > 0:
        return rooms
    return 1 if onsite(row, "CRDC_CTHRTZTN_LAB_SRVC_CD") else 0
