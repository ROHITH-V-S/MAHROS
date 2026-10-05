"""Generate docs/DATA_PROVENANCE.md from the live code and data.

    python experiments/build_provenance.py

This document is *generated*, never hand-written, and that is the point.

The previous version of this project claimed in its README to be "checked
against 3,186 real hospitals" while every hospital in every experiment was
synthesised from three hand-written templates. The claim and the code had
drifted apart, and a reviewer found the gap before the authors did.

A generated provenance document cannot drift. Every row below is read out of
the module that actually builds the network, or out of the vendored data file
that actually feeds it. If someone adds an assumed parameter and does not
declare it, it does not silently vanish from the paper -- it shows up here as
undeclared, and `test_provenance.py` fails.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mahros.calibration.facilities import (  # noqa: E402
    METROS,
    SUSPECTED_AGGREGATES,
    load_vendor,
)
from mahros.core.types import ResourceType  # noqa: E402
from mahros.sim.real_network import (  # noqa: E402
    PROVENANCE,
    build_real_network,
    provenance_table,
)
from mahros.sim.scenario import REAL_SCENARIOS, SCENARIOS  # noqa: E402

OUT = ROOT / "docs" / "DATA_PROVENANCE.md"


def main(metro: str = "houston") -> int:
    payload = load_vendor(metro)
    net = build_real_network(metro)
    src = payload["source"]
    sel = payload["selection"]
    counts = Counter(p.provenance for p in PROVENANCE)

    L: list[str] = []
    add = L.append

    add("# Data provenance")
    add("")
    add("> **Generated file.** Produced by `python experiments/build_provenance.py` "
        "from the code that builds the network and the data file that feeds it. "
        "Do not edit by hand.")
    add("")
    add("Every number the simulator consumes is classified as **observed** "
        "(traceable to a named field of a named public dataset), *derived* "
        "(computed from observed values by a stated rule), or _assumed_ "
        "(an author decision that no dataset supports).")
    add("")
    add(f"For the {sel['label']} network: **{counts['observed']} observed, "
        f"{counts['derived']} derived, {counts['assumed']} assumed.**")
    add("")

    # -- the source ------------------------------------------------------- #
    add("## 1. Source dataset")
    add("")
    add(f"- **Dataset** — {src['dataset']}")
    add(f"- **Publisher** — {src['publisher']}")
    add(f"- **Endpoint** — `{src['endpoint']}`")
    add(f"- **Licence** — {src['license']}")
    add(f"- **Dataset provenance flag** — `{src['dataset_provenance_flag']}`")
    add(f"- **Collection ended** — {src['collection_ended']}")
    add(f"- **Contains patient-level data** — "
        f"{'yes' if src['contains_patient_data'] else '**no**'}")
    add("")
    add(f"{src['note']}")
    add("")
    add(f"{src['suppression']}")
    add("")

    # -- selection -------------------------------------------------------- #
    add("## 2. Which hospitals, and who chose them")
    add("")
    add("These are author decisions, not facts the data dictates. They are "
        "listed so a reviewer can challenge them, and they are swept in "
        "sensitivity analysis.")
    add("")
    add("| Choice | Value |")
    add("|---|---|")
    add(f"| Study region | {sel['label']} |")
    add(f"| Catchment centre | ({sel['centroid_lat']}, {sel['centroid_lon']}) |")
    add(f"| Catchment radius | {sel['radius_km']:.0f} km |")
    add(f"| Minimum inpatient beds | {sel['min_inpatient_beds']:.0f} |")
    add(f"| Hospital subtypes kept | {', '.join(sel['subtypes'])} |")
    add(f"| Reference week | {sel['reference_week']} |")
    add(f"| Facilities selected | **{payload['n_facilities']}** |")
    add("")
    add(f"*Rationale.* {sel['rationale']}")
    add("")

    # -- exclusions ------------------------------------------------------- #
    add("### Facilities excluded by judgement")
    add("")
    add(f"{payload['excluded_note']}")
    add("")
    add("| Facility | CCN | Reason |")
    add("|---|---|---|")
    for e in payload["excluded"]:
        reason = e["reason"].replace("|", "\\|")
        add(f"| {e['name']} | `{e['ccn']}` | {reason} |")
    add("")
    if SUSPECTED_AGGREGATES:
        add(f"Of these, {len(SUSPECTED_AGGREGATES)} were removed as multi-site "
            f"system aggregates rather than by a mechanical threshold. That is "
            f"the most contestable call in the selection, so it is isolated in "
            f"`SUSPECTED_AGGREGATES` and can be re-included to show it does not "
            f"drive any result.")
        add("")

    # -- the provenance table --------------------------------------------- #
    add("## 3. Field-by-field provenance")
    add("")
    add(provenance_table())
    add("")
    add("### What the assumed rows mean")
    add("")
    add("The HHS dataset reports beds, ICU beds and occupancy. It reports "
        "**no** step-down beds, operating-room slots, ventilators or cath labs, "
        "and **no** specialty other than paediatric intensive care. Those are "
        "generated from declared ratios.")
    add("")
    add("The honest consequence: MAHROS's *capability* model — which hospital "
        "can treat what — is assumed rather than observed, except for "
        "paediatrics. Since capability is what makes an escalation transfer "
        "necessary at all, this is the most important remaining gap. Closing it "
        "means joining CMS Care Compare on the CCN, which every facility record "
        "already carries.")
    add("")

    # -- the network ------------------------------------------------------ #
    add("## 4. The network as instantiated")
    add("")
    add(f"{net.n_hospitals} real hospitals, week of {net.week}.")
    add("")
    add("| Hospital | CCN | Tier | Inpatient beds | Adult ICU | Observed occupancy |")
    add("|---|---|---|---|---|---|")
    for c in sorted(net.configs, key=lambda c: -net.observed[c.hospital_id]["inpatient_beds"]):
        o = net.observed[c.hospital_id]
        add(f"| {c.name} | `{c.hospital_id}` | {c.tier} | "
            f"{o['inpatient_beds']:.0f} | {o['adult_icu_beds']:.0f} | "
            f"{o['occupancy']:.3f} |")
    add("")
    tot_beds = sum(o["inpatient_beds"] for o in net.observed.values())
    tot_icu = sum(o["adult_icu_beds"] for o in net.observed.values())
    add(f"**Totals** — {tot_beds:,.0f} inpatient beds, {tot_icu:,.0f} staffed "
        f"adult ICU beds, all as reported.")
    add("")

    # -- scenarios -------------------------------------------------------- #
    add("## 5. Scenarios are weeks, not dials")
    add("")
    add("A synthetic scenario is made by turning a `surge_multiplier` up. A real "
        "scenario is made by choosing a week these hospitals actually lived "
        "through. Every week below was selected by observed network occupancy, "
        "restricted to weeks where at least 26 of 27 facilities reported bed "
        "counts and at least 22 reported ICU.")
    add("")
    add("| Scenario | Week | What it is |")
    add("|---|---|---|")
    descriptions = {
        "houston_calm": "Calmest fully-reported week in the series",
        "houston_baseline": "Reference week; also the HHS summary calibration week",
        "houston_typical": "Median week across the whole series",
        "houston_surge": "Delta wave peak, all 27 facilities reporting",
        "houston_post_pandemic": "Post-pandemic capacity crisis",
        "houston_icu_scarce": "ICU-scarcity **counterfactual** on real topology",
        "houston_smoke": "Fast smoke test",
    }
    for name, sc in REAL_SCENARIOS.items():
        add(f"| `{name}` | {sc.real_week} | {descriptions.get(name, '')} |")
    add("")
    add("Worth stating plainly, because it inverts an assumption the synthetic "
        "scenarios encoded: **the busiest weeks in this network are not the "
        "COVID waves.** Houston ran fuller in late 2023 than in January 2021. "
        "The synthetic scenarios were tuned toward 0.70 occupancy; the real "
        "network sat between 0.75 and 0.96.")
    add("")
    add("`houston_icu_scarce` is the one real-network scenario that is "
        "deliberately counterfactual: real hospitals and real geography, with "
        "capacity tightened to a critical-care-scarce regime. It is a "
        "*what-if*, not an observation of anywhere, and is labelled as such "
        "wherever it is reported.")
    add("")

    # -- what is still not validated -------------------------------------- #
    add("## 6. What this data cannot establish")
    add("")
    add("Stated here so it reaches a reader before a reviewer states it first.")
    add("")
    add("1. **Self-reported.** Hospitals reported to HHS via TeleTracking or "
        "their state health department. Not independently audited.")
    add("2. **The mandate ended 2024-05-03.** There is no facility-level "
        "successor; CDC's current NHSN respiratory dataset is "
        "jurisdiction-level. The evaluation window is 2020–2024.")
    add("3. **Excludes VA, Indian Health Service, DoD, psychiatric and "
        "rehabilitation facilities** by dataset design. A real Houston transfer "
        "network includes VA hospitals; this one cannot.")
    add("4. **Bed counts are 7-day averages of *staffed* beds**, so they move "
        "week to week and can exceed licensed capacity during a surge.")
    add("5. **No behaviour data.** Nothing here observes how hospitals respond "
        "to a transfer request, whether they refuse, or whether they refuse "
        "honestly. The strategic-refusal model is a declared assumption whose "
        "parameters are swept, and it is the central thing this project "
        "simulates rather than measures.")
    add("6. **No clinical timing data.** Deterioration timing, safe windows and "
        "length-of-stay distributions are modelling assumptions. Length of stay "
        "is scaled to a typical US acute figure rather than measured; "
        "calibrating it against HCUP or MIMIC-IV is follow-on work.")
    add("7. **Travel times are straight-line**, derived from real coordinates "
        "with a fixed handover overhead, not road-network routing.")
    add("")
    add("The claim this data *does* support is narrow and worth stating "
        "exactly: **the network's capacity, geography and load are real, and "
        "the simulation reproduces the occupancy these hospitals actually "
        "reported.** Everything about negotiation behaviour remains simulated.")
    add("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}  ({len(L)} lines)")
    print(f"  {counts['observed']} observed, {counts['derived']} derived, "
          f"{counts['assumed']} assumed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "houston"))
