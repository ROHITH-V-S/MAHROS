"""How often are the hospitals' own capacity reports unreliable?

    python experiments/data_integrity.py

Runs the Data Integrity Agent's rules (mahros/integrity/rules.py) over every
weekly report the Houston and Phoenix hospitals sent to HHS, and writes
results/data_integrity.json. This is the real-data reason MAHROS checks
claims instead of trusting them. A flag means a report is unreliable as
submitted; it does not say why, and it is not an accusation of dishonesty.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mahros.calibration.facilities import load_vendor  # noqa: E402
from mahros.integrity import check_series, summarise  # noqa: E402

RESULTS = ROOT / "results"


def audit(metro: str) -> dict:
    payload = load_vendor(metro)
    weeks = payload["weeks"]
    flags, reports = [], 0
    worst: dict[str, int] = {}
    for fac in payload["facilities"]:
        s = fac["series"]
        found = check_series(fac["ccn"], weeks, s["beds"], s["used"], s["icu"], s["icu_used"])
        flags += found
        reports += sum(1 for b in s["beds"] if b is not None)
        reds = sum(1 for f in found if f.severity == "red")
        if reds:
            worst[fac["name"].title()] = reds
    out = summarise(flags, reports)
    out["hospitals"] = len(payload["facilities"])
    out["share_flagged"] = out["flags"] / reports if reports else 0.0
    out["share_red"] = out["red"] / reports if reports else 0.0
    out["most_red"] = dict(sorted(worst.items(), key=lambda kv: -kv[1])[:5])
    out["examples"] = [f.as_row() for f in flags if f.severity == "red"][:5]
    return out


def main() -> int:
    out = {m: audit(m) for m in ("houston", "phoenix")}
    for m, r in out.items():
        print(f"{m:<8} {r['reports_checked']:>5} reports, {r['hospitals']} hospitals: "
              f"{r['flags']} flagged ({r['share_flagged']:.1%}), {r['red']} red "
              f"({r['share_red']:.1%}), {r['hospitals_with_red']} hospitals with a red")
        for rule, n in sorted(r["by_rule"].items(), key=lambda kv: -kv[1]):
            print(f"         {rule:<36}{n:>5}")
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "data_integrity.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("wrote results/data_integrity.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
