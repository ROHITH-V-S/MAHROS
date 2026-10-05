"""Fetch and vendor a real hospital network from HHS facility-level reporting.

    python experiments/fetch_network.py --metro houston
    python experiments/fetch_network.py --metro houston --week 2021-01-10
    python experiments/fetch_network.py --list

The repository ships the resulting JSON so every experiment runs offline. This
script is kept in the repo so the network is *reproducible* rather than
asserted: anyone can re-pull it from the federal API and diff the result.

Source: COVID-19 Reported Patient Impact and Hospital Capacity by Facility,
U.S. Department of Health & Human Services / CDC NHSN. Public domain.
Facility-level operational data; contains no patient-level information.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mahros.calibration.facilities import (  # noqa: E402
    METROS,
    load_vendor,
    num,
    vendor_path,
    write_vendor,
)

#: Default reference week. 2021-01-10 matches the week already used by the
#: existing summary-statistics calibration, so the two agree by construction.
DEFAULT_WEEK = "2021-01-10"


def summarise(payload: dict) -> str:
    facs = payload["facilities"]
    weeks = payload["weeks"]
    sel = payload["selection"]
    idx = weeks.index(sel["reference_week"]) if sel["reference_week"] in weeks else 0

    def at(f, key):
        return f["series"][key][idx]

    beds = [at(f, "beds") or 0.0 for f in facs]
    icu = [at(f, "icu") or 0.0 for f in facs]
    picu = sum(1 for f in facs if (at(f, "picu") or 0) > 0)
    no_icu = sum(1 for f in facs if not at(f, "icu"))

    lines = [
        f"{sel['label']}  --  {len(facs)} real hospitals",
        f"  reference week      : {sel['reference_week']}",
        f"  catchment           : {sel['radius_km']:.0f} km of "
        f"({sel['centroid_lat']:.4f}, {sel['centroid_lon']:.4f})",
        f"  bed floor           : {sel['min_inpatient_beds']:.0f} inpatient beds",
        f"  weeks of history    : {len(weeks)}  ({weeks[0]} -> {weeks[-1]})",
        f"  total inpatient beds: {sum(beds):,.0f}",
        f"  total adult ICU beds: {sum(icu):,.0f}",
        f"  with paediatric ICU : {picu}",
        f"  with no staffed ICU : {no_icu}",
        f"  excluded by judgement: {len(payload['excluded'])}",
        "",
        f"  {'hospital':<46}{'km':>6}{'beds':>7}{'ICU':>6}{'occ':>7}",
    ]
    for f in sorted(facs, key=lambda g: -(at(g, "beds") or 0)):
        b, u, i = at(f, "beds"), at(f, "used"), at(f, "icu")
        occ = f"{u / b:.2f}" if b and u else "   -"
        lines.append(f"  {f['name'][:44]:<46}{f['km_from_centroid']:6.1f}"
                     f"{(b or 0):7.0f}{(i or 0):6.0f}{occ:>7}")
    if payload["excluded"]:
        lines += ["", "  excluded (each with a recorded reason):"]
        for e in payload["excluded"]:
            lines.append(f"    - {e['name'][:50]} [{e['ccn']}]")
            lines.append(f"        {e['reason'][:100]}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--metro", default="houston", choices=sorted(METROS))
    ap.add_argument("--week", default=DEFAULT_WEEK,
                    help="reference week used to select and size the network")
    ap.add_argument("--list", action="store_true",
                    help="show the vendored network without re-fetching")
    args = ap.parse_args()

    if args.list:
        payload = load_vendor(args.metro)
        print(summarise(payload))
        return 0

    spec = METROS[args.metro]
    print(f"fetching {spec.label} from HHS facility reporting ...")
    out = write_vendor(spec, args.week)
    payload = load_vendor(args.metro)
    print(summarise(payload))
    size_kb = out.stat().st_size / 1024
    print(f"\nwrote {out.relative_to(ROOT)}  ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
