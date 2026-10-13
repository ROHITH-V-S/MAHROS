"""Real interhospital transfer case-mix: California, 2021.

    python experiments/fetch_transfer_mix.py           # download, summarise, vendor
    python experiments/fetch_transfer_mix.py --show    # print the vendored mix

Source: California Department of Health Care Access and Information (HCAI),
"Acute Care Hospital Transfers by Major Diagnostic Category (MDC)", built from
HCAI Patient Discharge Data, January-December 2021. Licence: Creative Commons
Attribution. 37,439 transfers.

Why this dataset. No public dataset records individual transfer *requests*
(who was asked, who refused). This one records what transfers actually
happened, by diagnosis, which is what MAHROS's escalation case-mix (which
patients need moving, and what for) was previously guessed from. Only the
counts are vendored, in mahros/calibration/data/ca_transfer_mix_2021.json.

Needs pandas and openpyxl (pip install -e ".[analysis]" openpyxl).
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "mahros" / "calibration" / "data" / "ca_transfer_mix_2021.json"
BASE = ("https://data.chhs.ca.gov/dataset/3f169603-e8f5-488d-bbbe-22c2c9c98b60/"
        "resource/fb239b03-d700-475c-9ef3-a4152df614f8/download/transfer-encounters.xlsx")
PAGE = "https://data.chhs.ca.gov/dataset/acute-care-hospital-transfers-by-major-diagnostic-category-mdc"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _download(url: str) -> bytes:
    """The portal redirects to a signed S3 link written as 's3.amazonaws.com:443'.

    S3 checks the signature against the Host header without the default port,
    but urllib copies the ':443' into it, so following the redirect normally
    gets a 403. Follow it by hand with the port removed.
    """
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(url, timeout=120) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307):
            raise
        location = e.headers["Location"]
    parts = urllib.parse.urlsplit(location)
    location = parts._replace(netloc=parts.netloc.removesuffix(":443")).geturl()
    with urllib.request.urlopen(location, timeout=120) as r:
        return r.read()


def fetch() -> dict:
    import pandas as pd

    frame = pd.read_excel(io.BytesIO(_download(BASE)))
    frame["n"] = pd.to_numeric(frame["TransferCount"], errors="coerce")
    overall = frame[frame["Category"] == "Overall"]
    by_mdc = {row.MDC_DESC.strip(): {"transfers": int(row.n),
                                     "share_of_discharges_transferred": float(row.TransferPercent)}
              for row in overall.itertuples()}
    age = frame[(frame["Category"] == "Age") & (frame["MDC_DESC"].str.strip() == "Overall")]
    by_age = {row.Subcategory: (None if row.n != row.n else int(row.n))
              for row in age.itertuples()}
    total = int(overall["n"].sum())
    discharges = float((overall["n"] / overall["TransferPercent"]).sum())
    return {
        "source": {
            "dataset": "Acute Care Hospital Transfers by Major Diagnostic Category (MDC)",
            "publisher": "California Department of Health Care Access and Information (HCAI)",
            "underlying": "HCAI Patient Discharge Data (PDD)",
            "coverage": "2021-01-01 to 2021-12-31",
            "licence": "Creative Commons Attribution",
            "page": PAGE,
            "file": BASE,
            "suppression": "Cells under 11 are published as 'Less than 11'; stored as null.",
        },
        "transfers_total": total,
        "transfer_rate_of_discharges": round(total / discharges, 5),
        "by_mdc": dict(sorted(by_mdc.items(), key=lambda kv: -kv[1]["transfers"])),
        "by_age_overall": by_age,
    }


def main(argv: list[str]) -> int:
    if "--show" not in argv:
        data = fetch()
        OUT.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"wrote {OUT.relative_to(ROOT)}")
    from mahros.calibration.case_mix import describe
    print(describe())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
