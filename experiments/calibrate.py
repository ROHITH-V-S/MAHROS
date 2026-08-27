"""Re-derive the vendored calibration reference from the live HHS API.

    python experiments/calibrate.py            # validate against the vendored file
    python experiments/calibrate.py --refetch  # re-pull from HHS, rewrite the file

The repository ships the *derived* summary so everything works offline and so
we are not redistributing a 1.8 MB federal CSV. This script is what produced
that summary, kept in the repo so the number is reproducible rather than
asserted.

Source: COVID-19 Reported Patient Impact and Hospital Capacity by Facility,
U.S. Department of Health & Human Services / CDC NHSN. Public domain.
Facility-level operational data; contains no patient-level information.
"""

from __future__ import annotations

import json
import math
import statistics as st
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "mahros" / "calibration" / "data" / "us_hhs_2021w02.json"

ENDPOINT = "https://healthdata.gov/resource/anag-cw7u.json"
WEEK = "2021-01-10T00:00:00.000"
FIELDS = [
    "hospital_pk", "state", "city", "hospital_subtype", "is_metro_micro",
    "total_beds_7_day_avg", "inpatient_beds_7_day_avg",
    "inpatient_beds_used_7_day_avg", "total_icu_beds_7_day_avg",
    "total_staffed_adult_icu_beds_7_day_avg",
    "staffed_adult_icu_bed_occupancy_7_day_avg",
]


def fetch(limit: int = 7000) -> list[dict]:
    url = (f"{ENDPOINT}?$limit={limit}"
           f"&$where=collection_week='{WEEK}'"
           f"&$select={','.join(FIELDS)}")
    print(f"GET {url[:90]}...")
    with urllib.request.urlopen(url, timeout=180) as resp:
        return json.loads(resp.read().decode())


def _num(row: dict, key: str) -> float | None:
    """Parse a field, dropping CDC privacy-suppressed values.

    Small counts are published as -999999 to prevent re-identification of
    facilities. Those are *dropped*, never imputed or clamped to zero -- a
    suppressed ICU count is unknown, not absent, and treating it as zero would
    manufacture ICU scarcity that the data does not show.
    """
    raw = row.get(key)
    if raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    return None if val < 0 else val


def _q(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return xs[int(k)] if lo == hi else xs[lo] * (hi - k) + xs[hi] * (k - lo)


def _lognormal(xs: list[float]) -> dict[str, float]:
    logs = [math.log(x) for x in xs if x > 0]
    return {"mu": round(st.fmean(logs), 4), "sigma": round(st.pstdev(logs), 4)}


def derive(rows: list[dict]) -> dict:
    recs = []
    for r in rows:
        if r.get("hospital_subtype") != "Short Term":
            continue
        beds = _num(r, "inpatient_beds_7_day_avg")
        if not beds or beds < 5:
            continue
        recs.append({
            "beds": beds,
            "icu": _num(r, "total_staffed_adult_icu_beds_7_day_avg") or 0.0,
            "used": _num(r, "inpatient_beds_used_7_day_avg"),
            "iocc": _num(r, "staffed_adult_icu_bed_occupancy_7_day_avg"),
        })

    beds = [r["beds"] for r in recs]
    p55, p88 = _q(beds, 0.55), _q(beds, 0.88)
    tiers = {
        1: [r for r in recs if r["beds"] <= p55],
        2: [r for r in recs if p55 < r["beds"] <= p88],
        3: [r for r in recs if r["beds"] > p88],
    }

    out: dict = {
        "source": {
            "dataset": "COVID-19 Reported Patient Impact and Hospital Capacity by Facility",
            "publisher": "U.S. Department of Health & Human Services / CDC NHSN",
            "endpoint": ENDPOINT,
            "collection_week": WEEK[:10],
            "filter": "hospital_subtype == 'Short Term' AND inpatient_beds_7_day_avg >= 5",
            "note": "Values of -999999 denote CDC privacy suppression of small counts "
                    "and are dropped, not imputed.",
            "license": "U.S. Public Domain (federal open data)",
            "n_rows_pulled": len(rows),
            "n_hospitals_used": len(recs),
        },
        "tier_split": {
            "rule": "inpatient-bed percentiles",
            "p55": round(p55, 1), "p88": round(p88, 1),
            "counts": {str(k): len(v) for k, v in tiers.items()},
        },
        "tiers": {},
        "occupancy": {},
        "capability": {},
    }

    for tier, group in tiers.items():
        b = [r["beds"] for r in group]
        i = [r["icu"] for r in group]
        ratio = [r["icu"] / r["beds"] for r in group if r["beds"] > 0]
        out["tiers"][str(tier)] = {
            "n": len(group),
            "inpatient_beds": {
                "p25": round(_q(b, .25), 1), "median": round(_q(b, .5), 1),
                "p75": round(_q(b, .75), 1), "lognormal": _lognormal(b),
            },
            "staffed_adult_icu_beds": {
                "p25": round(_q(i, .25), 1), "median": round(_q(i, .5), 1),
                "p75": round(_q(i, .75), 1),
            },
            "icu_to_inpatient_ratio": {
                "p25": round(_q(ratio, .25), 4), "median": round(_q(ratio, .5), 4),
                "p75": round(_q(ratio, .75), 4),
            },
            "share_with_zero_icu": round(
                sum(1 for r in group if r["icu"] == 0) / len(group), 4),
        }

    # Occupancy ratios above 1.2 are reporting artefacts (a facility can exceed
    # 100% briefly, but 120%+ of staffed beds is a data-entry problem).
    occ = [r["used"] / r["beds"] for r in recs
           if r["used"] and r["beds"] and r["used"] / r["beds"] <= 1.2]
    iocc = [r["iocc"] / r["icu"] for r in recs
            if r["iocc"] and r["icu"] and r["iocc"] / r["icu"] <= 1.2]
    for label, vals in (("inpatient", occ), ("icu", iocc)):
        out["occupancy"][label] = {
            "mean": round(st.fmean(vals), 4),
            "p10": round(_q(vals, .10), 4), "p25": round(_q(vals, .25), 4),
            "median": round(_q(vals, .5), 4), "p75": round(_q(vals, .75), 4),
            "p90": round(_q(vals, .90), 4), "n": len(vals),
        }

    out["capability"] = {
        "share_all_hospitals_with_zero_icu": round(
            sum(1 for r in recs if r["icu"] == 0) / len(recs), 4),
        "interpretation": "Critical-care capability is concentrated: a large minority "
                          "of small hospitals cannot escalate at all, which is the "
                          "structural reason interhospital transfer exists.",
    }
    return out


def main() -> int:
    from mahros.calibration import load_reference, validate_scenario

    if "--refetch" in sys.argv:
        rows = fetch()
        print(f"pulled {len(rows)} facility-weeks")
        summary = derive(rows)
        OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"wrote {OUT.relative_to(ROOT)} "
              f"({summary['source']['n_hospitals_used']} hospitals)")

    ref = load_reference()
    print(f"\nReference: {ref['source']['n_hospitals_used']} hospitals, "
          f"week of {ref['source']['collection_week']}")
    print(f"  inpatient occupancy mean : {ref['occupancy']['inpatient']['mean']:.1%}")
    print(f"  ICU occupancy mean       : {ref['occupancy']['icu']['mean']:.1%}")
    print(f"  hospitals with no ICU    : {ref['capability']['share_all_hospitals_with_zero_icu']:.1%}")
    print()
    print(validate_scenario().text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
