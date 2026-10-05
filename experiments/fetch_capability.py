"""Vendor hospital capability from the CMS Provider of Services file.

    python experiments/fetch_capability.py --metro houston
    python experiments/fetch_capability.py --list          # show what is vendored
    python experiments/fetch_capability.py --csv PATH      # use an existing download

Capability decides who can bid at all, and it was the largest assumed block in
the model. This replaces it with the federal certification record.

Source: CMS Provider of Services (POS), Q1 2021, via the NBER mirror at
https://data.nber.org/pos/ -- data.cms.gov refuses automated requests. Joined on
CCN, which both datasets carry, so the match is exact.

The download is ~109 MB and is only needed to re-vendor; the extracted records
are small and ship with the repository.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mahros.calibration.capability import (  # noqa: E402
    DEFAULT_QUARTER,
    build_payload,
    capability_path,
    cath_lab_rooms,
    download,
    extract,
    load_capability,
    onsite,
    pos_url,
)
from mahros.calibration.facilities import METROS, load_vendor  # noqa: E402

SHOW = [
    ("cath rooms", lambda r: str(cath_lab_rooms(r))),
    ("cardiac sx", lambda r: "yes" if onsite(r, "OPEN_HRT_SRGRY_SRVC_CD") else "-"),
    ("neurosurg", lambda r: "yes" if onsite(r, "NRSRGCL_SRVC_CD") else "-"),
    ("trauma", lambda r: "yes" if onsite(r, "SHCK_TRMA_SRVC_CD") else "-"),
    ("obstetric", lambda r: "yes" if onsite(r, "OB_SRVC_CD") else "-"),
    ("paeds ICU", lambda r: "yes" if onsite(r, "PED_ICU_SRVC_CD") else "-"),
    ("burns", lambda r: "yes" if onsite(r, "BURN_CARE_UNIT_SRVC_CD") else "-"),
]


def show(metro: str) -> int:
    payload = load_capability(metro)
    if payload is None:
        print(f"nothing vendored for {metro}; run without --list first")
        return 2
    rows = payload["facilities"]
    print(f"\n{payload['source']['dataset']}  {payload['source']['quarter']}")
    print(f"{payload['n_facilities']} facilities, joined on "
          f"{payload['source']['join_key']}\n")
    head = f"  {'hospital':<34}" + "".join(f"{label:>11}" for label, _ in SHOW)
    print(head)
    for ccn, r in sorted(rows.items(), key=lambda kv: -int(kv[1]["BED_CNT"] or 0)):
        line = f"  {r['FAC_NAME'][:32]:<34}"
        line += "".join(f"{fn(r):>11}" for _, fn in SHOW)
        print(line)

    print(f"\n  totals across {len(rows)} facilities:")
    for label, fn in SHOW:
        n = sum(1 for r in rows.values() if fn(r) not in ("-", "0"))
        print(f"    {label:<12} {n:>3} facilities")
    print("\n  still assumed (not in this dataset):")
    for k, v in payload["source"]["not_available"].items():
        print(f"    {k}: {v}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--metro", default="houston", choices=sorted(METROS))
    ap.add_argument("--quarter", default=DEFAULT_QUARTER)
    ap.add_argument("--csv", help="path to an already-downloaded POS csv")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        return show(args.metro)

    ccns = {f["ccn"] for f in load_vendor(args.metro)["facilities"]}

    if args.csv:
        csv_path = Path(args.csv)
    else:
        csv_path = Path(tempfile.gettempdir()) / f"pos_{args.quarter}.csv"
        if not csv_path.exists():
            print(f"downloading {pos_url(args.quarter)}")
            print("  (~109 MB, a few minutes)")
            download(args.quarter, csv_path)
        print(f"using {csv_path} ({csv_path.stat().st_size / 1e6:.0f} MB)")

    rows = extract(csv_path, ccns)
    print(f"matched {len(rows)} of {len(ccns)} CCNs")
    if len(rows) < len(ccns):
        print("  MISSING:", sorted(ccns - set(rows)))

    payload = build_payload(args.metro, args.quarter, rows)
    out = capability_path(args.metro)
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size / 1024:.0f} KB)")
    return show(args.metro)


if __name__ == "__main__":
    raise SystemExit(main())
