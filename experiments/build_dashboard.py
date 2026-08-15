"""Build the self-contained dashboard: template + results -> one HTML file.

    python experiments/run_all.py 5          # produces results/dashboard_data.json
    python experiments/build_dashboard.py    # produces results/dashboard.html

Kept as a build step rather than a hand-edited file so the dashboard can never
drift out of sync with the numbers it is reporting. Re-running the sweep and
rebuilding is the only way to change what it shows.

The output embeds its data inline and loads nothing over the network, so it
opens straight from disk and can be published as a static page unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "dashboard" / "template.html"
DATA = ROOT / "results" / "dashboard_data.json"
OUT = ROOT / "results" / "dashboard.html"

TOKEN = "__MAHROS_DATA__"

#: Only these keys are embedded. Keeps the page small and makes it explicit
#: that nothing patient-level (even synthetic) ships inside the artefact.
KEEP = [
    "meta", "results", "deltas", "ablation",
    "hospitals", "hospitals_no_fairness",
    "sample_decisions", "by_acuity",
    "ledger_demo", "privacy_demo",
]


def main() -> None:
    if not DATA.exists():
        raise SystemExit(
            f"missing {DATA}\nrun:  python experiments/run_all.py 5"
        )

    full = json.loads(DATA.read_text(encoding="utf-8"))
    payload = {k: full[k] for k in KEEP if k in full}

    template = TEMPLATE.read_text(encoding="utf-8")
    if TOKEN not in template:
        raise SystemExit(f"{TEMPLATE} no longer contains {TOKEN}")

    # separators without spaces: the payload is machine-read, not human-read
    blob = json.dumps(payload, separators=(",", ":"), default=str)
    # </script> inside a JSON string would close the tag early
    blob = blob.replace("</", "<\\/")

    html = template.replace(TOKEN, blob)
    # newline="" keeps LF endings on Windows: the file is served as-is to a
    # browser, and CRLF inside the embedded script trips naive tooling.
    with open(OUT, "w", encoding="utf-8", newline="") as fh:
        fh.write(html)

    print(f"wrote {OUT}")
    print(f"  page   {len(html) / 1024:.0f} KB")
    print(f"  data   {len(blob) / 1024:.0f} KB embedded")
    print(f"  seeds  {payload['meta']['n_seeds']}")
    print(f"  built  {payload['meta']['generated_at']}")


if __name__ == "__main__":
    main()
