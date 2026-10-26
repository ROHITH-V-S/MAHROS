"""Write the pgfplots data tables used by the paper from results/*.json.

    python paper/scripts/make_figure_data.py

Every plotted number in the paper comes from this script, which reads only the
generated experiment outputs in results/. Nothing is typed in by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "results"
OUT = ROOT / "paper" / "figures" / "data"


def load(name: str) -> dict:
    return json.loads((RES / name).read_text(encoding="utf-8"))


def write(name: str, header: list[str], rows: list[list]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    lines = [" ".join(header)]
    for r in rows:
        lines.append(" ".join(f"{v:.6g}" if isinstance(v, float) else str(v) for v in r))
    (OUT / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {name} ({len(rows)} rows)")


def occupancy() -> None:
    for scen in ("houston_baseline", "houston_surge", "phoenix_baseline"):
        d = load(f"validate_occupancy_{scen}.json")
        rows = [[f["observed"], f["simulated"], int(bool(f["capped"]))]
                for f in d["facilities"]]
        write(f"occ_{scen}.dat", ["observed", "simulated", "capped"], rows)


def honest_weeks() -> None:
    res = load("dashboard_data.json")["results"]
    weeks = ["houston_calm", "houston_baseline", "houston_typical",
             "houston_surge", "houston_post_pandemic"]
    rows = []
    for i, w in enumerate(weeks):
        r = res[w]
        rows.append([i, *(100 * r[s]["success_rate"] for s in
                          ("mahros", "phone", "central", "optimal", "nearest", "none"))])
    write("honest_weeks.dat",
          ["idx", "mahros", "phone", "central", "optimal", "registry", "none"], rows)


def dose_response() -> None:
    d = load("adversarial.json")["sweeps"]["dose_response"]
    reg = load("registry_attack.json")["scenarios"]["houston_surge"]
    rows = []
    for k in sorted(d, key=float):
        registry = 100 * reg[k]["registry"] if k in reg else float("nan")
        rows.append([100 * float(k), 100 * d[k]["off"]["success_rate"],
                     100 * d[k]["on"]["success_rate"], registry])
    write("dose_response.dat", ["protective", "off", "on", "registry"], rows)


def attestation() -> None:
    d = load("attestation.json")
    rows = [[float(k), 100 * v["success_rate"], v["signed"], v["declined"], v["referrals"]]
            for k, v in sorted(d["sweep"].items(), key=lambda kv: float(kv[0]))]
    write("attestation.dat", ["deterrence", "success", "signed", "declined", "referrals"], rows)


def collusion() -> None:
    d = load("collusion.json")
    write("cartel_size.dat", ["size", "success"],
          [[int(k), 100 * v["success_rate"]] for k, v in
           sorted(d["attack"].items(), key=lambda kv: int(kv[0]))])
    write("spot_checks.dat", ["rate", "honest", "colluding"],
          [[100 * float(k), 100 * v["honest"]["success_rate"],
            100 * v["colluding"]["success_rate"]]
           for k, v in sorted(d["defence"].items(), key=lambda kv: float(kv[0]))])


def sensitivity() -> None:
    d = load("sensitivity.json")
    sweeps = d.get("sweeps", d)

    def find(key: str) -> dict:
        for k, v in sweeps.items():
            if k.replace(" ", "_") == key:
                return v
        raise KeyError(key)

    sw = find("safe_window_scale")
    write("sens_window.dat", ["scale", "mahros", "phone", "central"],
          [[float(k), 100 * v["mahros"], 100 * v["phone"], 100 * v["central"]]
           for k, v in sorted(sw.items(), key=lambda kv: float(kv[0]))])
    cd = find("call_duration")
    write("sens_call.dat", ["minutes", "phone"],
          [[float(k), 100 * v] for k, v in sorted(cd.items(), key=lambda kv: float(kv[0]))])
    st = find("phone_staleness")
    write("sens_stale.dat", ["staleness", "phone"],
          [[float(k), 100 * v] for k, v in sorted(st.items(), key=lambda kv: float(kv[0]))])


def by_acuity() -> None:
    d = load("dashboard_data.json")["by_acuity"]
    rows = []
    for a in ("2", "3", "4", "5"):
        rows.append([int(a), *(100 * d[s][a]["success"] for s in ("mahros", "phone", "central"))])
    write("by_acuity.dat", ["acuity", "mahros", "phone", "central"], rows)


def wait_cdf() -> None:
    """Percentiles of time to care for completed transfers (surge week, seed 42)."""
    w = load("dashboard_data.json")["wait_distribution"]
    rows = []
    for pct in range(1, 100):
        row = [pct / 100]
        for s in ("mahros", "phone", "central"):
            xs = sorted(w[s])
            row.append(xs[min(len(xs) - 1, int(pct / 100 * len(xs)))])
        rows.append(row)
    write("wait_cdf.dat", ["p", "mahros", "phone", "central"], rows)


def burden() -> None:
    """Acceptances per 100 critical-care units, per hospital, fairness on/off."""
    d = load("dashboard_data.json")
    off = {h["id"]: h for h in d["hospitals_no_fairness"]}
    hs = sorted(d["hospitals"], key=lambda h: h["critical_capacity"])
    rows = []
    for i, h in enumerate(hs, start=1):
        cap = max(h["critical_capacity"], 1)
        rows.append([i, h["critical_capacity"], 100 * h["accepted"] / cap,
                     100 * off[h["id"]]["accepted"] / cap])
    write("burden.dat", ["rank", "capacity", "fair", "nofair"], rows)


if __name__ == "__main__":
    wait_cdf()
    burden()
    occupancy()
    honest_weeks()
    dose_response()
    attestation()
    collusion()
    sensitivity()
    by_acuity()
