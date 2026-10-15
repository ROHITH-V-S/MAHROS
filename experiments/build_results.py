"""Write every result into docs/RESULTS.md, the README and the panel guide.

    python experiments/build_results.py

Numbers are read from results/*.json and nowhere else. The README and
docs/PANEL_GUIDE.md each carry a block between markers that this script
rewrites; the rest of those files is hand-written and quotes no results.
That is the fix for numbers that used to be copied by hand into four
documents and drift apart.

A result file that does not exist yet is reported as "not built", never
filled in from memory.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "results"
OUT = ROOT / "docs" / "RESULTS.md"


def load(name: str) -> dict | None:
    path = RES / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x * 100:.{digits}f}%"


def pts(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x * 100:+.{digits}f} pts"


def pval(p: float | None) -> str:
    if p is None:
        return "—"
    return "p < 0.0001" if p < 0.0001 else f"p = {p:.4f}"


def missing(script: str) -> str:
    return f"*Not built yet: run `python experiments/{script}`.*\n"


STRATEGY_NAMES = {"mahros": "MAHROS", "phone": "Phone tree", "central": "Central greedy",
                  "optimal": "Batched optimal", "nearest": "Shared registry",
                  "none": "No transfers"}


def main() -> int:
    L: list[str] = []
    add = L.append
    headline: dict[str, str] = {}

    add("# Results")
    add("")
    add("> **Generated** by `python experiments/build_results.py` from "
        "`results/*.json`. Do not edit by hand; re-run the experiments and "
        "this script.")
    add("")
    add("Network: 27 real Houston hospitals (headline) and 30 real Phoenix "
        "hospitals (replication). Behaviour is simulated. Every run starts "
        "from the occupancy the hospitals reported that week, uses the real "
        "California 2021 transfer case-mix, and is reproducible from "
        "`(scenario, seed)`.")
    add("")

    # -- 1. validation --------------------------------------------------- #
    add("## 1. Does the simulation reproduce real occupancy?")
    add("")
    add("Cold start (empty network), so this tests where the fitted model "
        "goes, not where it was put. Phoenix was fitted by the same code with "
        "nothing tuned.")
    add("")
    rows = []
    for sc in ("houston_baseline", "houston_surge", "phoenix_baseline"):
        v = load(f"validate_occupancy_{sc}.json")
        if v:
            rows.append(f"| `{sc}` ({v['week']}) | {len(v['facilities'])} | "
                        f"{v['mae'] * 100:.2f} pts | {v['r2']:.3f} | "
                        f"{v['mae_vs_fitted_target'] * 100:.2f} pts | {v['n_capped']} | {v['verdict']} |")
    if rows:
        add("| Scenario | Hospitals | Error vs reported | R² | Error vs fitted target | Reported above 0.95 ceiling | Verdict |")
        add("|---|---|---|---|---|---|---|")
        L.extend(rows)
        hb = load("validate_occupancy_houston_baseline.json")
        ph = load("validate_occupancy_phoenix_baseline.json")
        if hb and ph:
            headline["validation"] = (
                f"Simulated occupancy is within {hb['mae'] * 100:.1f} points of what "
                f"Houston's hospitals reported (R² {hb['r2']:.2f}), and "
                f"{ph['mae'] * 100:.1f} points for Phoenix (R² {ph['r2']:.2f}), "
                f"which was not tuned.")
    else:
        add(missing("validate_occupancy.py houston_baseline 5"))
    add("")

    # -- 2. data integrity ------------------------------------------------ #
    add("## 2. How reliable are the hospitals' own capacity reports?")
    add("")
    di = load("data_integrity.json")
    if di:
        add("The Data Integrity Agent's rules over every weekly report the "
            "hospitals sent to HHS. **Red** = cannot be right as reported. "
            "**Amber** = looks copied or stale. A flag means unreliable, not "
            "dishonest.")
        add("")
        add("| City | Reports | Flagged | Red | Hospitals with a red |")
        add("|---|---|---|---|---|")
        for city, r in di.items():
            add(f"| {city.title()} | {r['reports_checked']:,} | {r['flags']} "
                f"({pct(r['share_flagged'])}) | {r['red']} ({pct(r['share_red'])}) | "
                f"{r['hospitals_with_red']} of {r['hospitals']} |")
        add("")
        add("| Rule | Houston | Phoenix |")
        add("|---|---|---|")
        rules = sorted(set(di["houston"]["by_rule"]) | set(di["phoenix"]["by_rule"]))
        for rule in rules:
            add(f"| {rule.replace('_', ' ')} | {di['houston']['by_rule'].get(rule, 0)} | "
                f"{di['phoenix']['by_rule'].get(rule, 0)} |")
        add("")
        add("Reliability varies more than tenfold between the two regions, so "
            "neither rate is a national estimate.")
        h, p = di["houston"], di["phoenix"]
        headline["integrity"] = (
            f"{pct(h['share_flagged'])} of Houston's weekly capacity reports and "
            f"{pct(p['share_flagged'])} of Phoenix's are impossible or look copied "
            f"({h['hospitals_with_red']} of {h['hospitals']} and "
            f"{p['hospitals_with_red']} of {p['hospitals']} hospitals have at least one "
            f"report that cannot be right).")
    else:
        add(missing("data_integrity.py"))
    add("")

    # -- 3. coordination ladder ----------------------------------------- #
    add("## 3. Coordination on an honest network, Houston")
    add("")
    dash = load("dashboard_data.json")
    if dash:
        strategies = dash["meta"]["strategies"]
        add(f"Transfer success rate, {dash['meta']['n_seeds']} seeds per cell. "
            "The phone tree has perfect information (no stale answers).")
        add("")
        add("| Week | " + " | ".join(STRATEGY_NAMES[s] for s in strategies) + " |")
        add("|---|" + "---|" * len(strategies))
        for sc in dash["meta"]["scenarios"]:
            r = dash["results"][sc]
            add(f"| `{sc}` | " + " | ".join(pct(r[s]["success_rate"]) for s in strategies) + " |")
        add("")
        surge = dash["results"]["houston_surge"]
        add("Surge week, other measures:")
        add("")
        add("| | " + " | ".join(STRATEGY_NAMES[s] for s in strategies if s != "none") + " |")
        add("|---|" + "---|" * (len(strategies) - 1))
        for key, label, fmt in (("mean_wait", "Mean time to care (min)", "{:.1f}"),
                                ("coordinator_minutes_per_transfer",
                                 "Coordinator minutes per transfer", "{:.1f}"),
                                ("burden_gini", "Burden Gini (0 = even)", "{:.3f}")):
            if key in surge["mahros"]:
                add(f"| {label} | " + " | ".join(
                    fmt.format(surge[s][key]) for s in strategies if s != "none") + " |")
        add("")
        ab = dash.get("ablation")
        if ab:
            add(f"### Ablation (`{dash.get('ablation_scenario')}`)")
            add("")
            add("| Arm | Success | Burden Gini | Mean time to care |")
            add("|---|---|---|---|")
            for label, a in ab.items():
                add(f"| {label.replace('_', ' ')} | {pct(a['success_rate'])} | "
                    f"{a['burden_gini']:.3f} | {a['mean_wait']:.1f} min |")
            add("")
            add("The adversarial arms have 50% protective hospitals. Removing the "
                "ledger removes the evidence a refusal is checked against, which "
                "is why it costs as much as removing the checking itself.")
            headline["ablation"] = (
                f"With half the hospitals protective, checking refusals keeps success at "
                f"{pct(ab['adversarial_full']['success_rate'])}; without checking it falls to "
                f"{pct(ab['adversarial_no_argumentation']['success_rate'])}, and without the "
                f"ledger to {pct(ab['adversarial_no_audit']['success_rate'])}.")
            add("")
        if "coordinator_minutes_per_transfer" in surge["mahros"]:
            headline["staff"] = (
                f"Coordinator time per transfer: {surge['mahros']['coordinator_minutes_per_transfer']:.1f} "
                f"min with MAHROS, {surge['phone']['coordinator_minutes_per_transfer']:.1f} min by phone.")
    else:
        add(missing("run_all.py 5"))
    add("")

    # -- 4. significance --------------------------------------------------- #
    add("## 4. What may be claimed (paired tests)")
    add("")
    sig = load("significance.json")
    if sig:
        m = sig["meta"]
        add(f"`{m['scenario']}`, {m['n_seeds']} paired seeds, Holm-corrected across "
            f"four comparisons; equivalence margin ±{m['equivalence_margin'] * 100:.0f} "
            "points, declared before the analysis.")
        add("")
        add("| MAHROS minus | Difference | 95% CI | Holm-adjusted | Equivalent (TOST)? |")
        add("|---|---|---|---|---|")
        for other in ("phone", "central", "optimal", "nearest"):
            d = sig["differences"][f"mahros_vs_{other}"]
            eq = sig["equivalence"].get(f"equivalence_vs_{other}")
            eqs = "—" if eq is None else (f"**yes**, {pval(eq['p_value'])}"
                                         if eq["equivalent_at_05"] else f"no, {pval(eq['p_value'])}")
            add(f"| {STRATEGY_NAMES[other]} | {pts(d['mean_difference'])} | "
                f"[{d['ci95'][0] * 100:+.1f}, {d['ci95'][1] * 100:+.1f}] | "
                f"{pval(sig['holm_adjusted'][f'mahros_vs_{other}'])} | {eqs} |")
        add("")
        f = sig["fairness"]
        add(f"**Fairness layer** (on minus off): burden Gini {f['gini']['mean_difference']:+.3f} "
            f"({pval(f['gini']['p_value'])}); success {pts(f['success']['mean_difference'])} "
            f"({pval(f['success']['p_value'])}); time to care "
            f"{f['wait']['mean_difference']:+.1f} min ({pval(f['wait']['p_value'])}).")
        add("")
        dc = sig["deliberation_cost"]
        add(f"**Cost of checking refusals on an honest network**: success "
            f"{pts(dc['success']['mean_difference'])} ({pval(dc['success']['p_value'])}); "
            f"time to care {dc['wait']['mean_difference']:+.1f} min ({pval(dc['wait']['p_value'])}).")
        d = sig["differences"]
        headline["phone"] = (
            f"MAHROS places {d['mahros_vs_phone']['mean_difference'] * 100:.1f} percentage "
            f"points more patients in time than a phone tree given perfect information "
            f"({pval(sig['holm_adjusted']['mahros_vs_phone'])}).")
        eqc, eqo = sig["equivalence"]["equivalence_vs_central"], sig["equivalence"]["equivalence_vs_optimal"]
        headline["central"] = (
            f"Against optimisers that see every hospital's true occupancy: "
            f"{pts(d['mahros_vs_central']['mean_difference'])} vs central greedy, "
            f"{pts(d['mahros_vs_optimal']['mean_difference'])} vs batched optimal; "
            f"equivalent within ±2 points: {'yes' if eqc['equivalent_at_05'] else 'no'} / "
            f"{'yes' if eqo['equivalent_at_05'] else 'no'}.")
        headline["registry_honest"] = (
            f"On an honest network a shared registry ties MAHROS "
            f"({pts(d['mahros_vs_nearest']['mean_difference'])}, "
            f"Holm {pval(sig['holm_adjusted']['mahros_vs_nearest'])}).")
    else:
        add(missing("significance.py 12"))
    add("")

    # -- 5. adversarial ---------------------------------------------------- #
    add("## 5. When hospitals refuse falsely")
    add("")
    adv = load("adversarial.json")
    if adv:
        curve = adv["sweeps"]["dose_response"]
        base = curve["0.00"]["off"]["success_rate"]
        add(f"`{adv['meta']['scenario']}`, {adv['meta']['n_seeds']} seeds. Protective "
            "hospitals say \"full\" for patients they do not want (never for acuity 5). "
            "Checking ON is the unattested protocol, which assumes a liar cannot "
            "defend itself; see section 7 for the version without that assumption.")
        add("")
        add("| Protective | Checking OFF | Checking ON | Share of the loss recovered | False refusals | Overruled | False accusations |")
        add("|---|---|---|---|---|---|---|")
        for frac, row in curve.items():
            off, on = row["off"]["success_rate"], row["on"]["success_rate"]
            lost = base - off
            rec = f"{(on - off) / lost:.0%}" if lost > 0.001 else "—"
            add(f"| {float(frac):.0%} | {pct(off)} | {pct(on)} | {rec} | "
                f"{row['on']['misreports']:.0f} | {row['on']['overruled']:.1f} | "
                f"{row['on']['false_accusations']:.1f} |")
        add("")
        add("Safety check (cautious hospitals refuse more, but truthfully):")
        add("")
        add("| Network | Challenges | Overruled | False accusations |")
        add("|---|---|---|---|")
        for label, s in adv["safety"].items():
            add(f"| {label} | {s['challenges']:.0f} | {s['overruled']:.1f} | "
                f"{s['false_accusations']:.1f} |")
        half = curve.get("0.50")
        if half:
            headline["adversarial"] = (
                f"With half the hospitals refusing falsely, success falls to "
                f"{pct(half['off']['success_rate'])} without checking and holds at "
                f"{pct(half['on']['success_rate'])} with it.")
    else:
        add(missing("adversarial.py 6"))
    add("")

    # -- 6. registry under attack ----------------------------------------- #
    add("## 6. Why not a shared registry? The same lies, both methods")
    add("")
    reg = load("registry_attack.json")
    if reg:
        add("| Network | Protective | Registry | MAHROS | MAHROS minus registry |")
        add("|---|---|---|---|---|")
        for sc, rows_ in reg["scenarios"].items():
            for frac, r in rows_.items():
                add(f"| `{sc}` | {float(frac):.0%} | {pct(r['registry'])} | "
                    f"{pct(r['mahros'])} | {pts(r['diff'])} ({pval(r['p'])}) |")
        h = reg["scenarios"].get("houston_surge", {}).get("0.50")
        if h:
            headline["registry_attack"] = (
                f"With half the hospitals refusing falsely, a shared registry places "
                f"{pct(h['registry'])} and MAHROS {pct(h['mahros'])} ({pts(h['diff'])}).")
    else:
        add(missing("registry_attack.py 6"))
    add("")

    # -- 7. attestation ---------------------------------------------------- #
    add("## 7. Signed refusals: removing the \"liars can't defend\" assumption")
    add("")
    att = load("attestation.json")
    if att:
        add(f"`{att['meta']['scenario']}`, {att['meta']['strategic_fraction']:.0%} protective, "
            f"{len(att['meta']['seeds'])} seeds; signatures: {att['meta']['signature_backend']}. "
            "Deterrence = probability a lying hospital declines to sign. Nothing "
            "measures it, so the whole range is the result.")
        add("")
        add("| Arm | Success | False accusations |")
        add("|---|---|---|")
        add(f"| No checking (plain Contract Net) | {pct(att['arms']['plain_contract_net']['success_rate'])} | "
            f"{att['arms']['plain_contract_net']['false_accusations']:.1f} |")
        add(f"| Unattested (liar assumed unable to defend) | {pct(att['arms']['unattested']['success_rate'])} | "
            f"{att['arms']['unattested']['false_accusations']:.1f} |")
        for d, a in att["sweep"].items():
            add(f"| Signed, deterrence {float(d):.2f} | {pct(a['success_rate'])} | "
                f"{a['false_accusations']:.1f} |")
        add("")
        et = att["endpoint_tests"]
        add(f"Endpoints: deterrence 1.0 vs unattested {pts(et['vs_unattested_at_1']['mean_difference'])} "
            f"({pval(et['vs_unattested_at_1']['p_value'])}); deterrence 0.0 vs no checking "
            f"{pts(et['vs_plain_at_0']['mean_difference'])} ({pval(et['vs_plain_at_0']['p_value'])}). "
            f"Audit-referral false-positive rate: {pct(att['referral_false_positive_rate'], 2)}.")
        lo, hi = att["sweep"]["0.00"]["success_rate"], att["sweep"]["1.00"]["success_rate"]
        headline["attestation"] = (
            f"Requiring hospitals to sign their refusals gives {pct(lo)} to {pct(hi)} "
            f"success depending on how often a liar refuses to sign (no checking: "
            f"{pct(att['arms']['plain_contract_net']['success_rate'])}).")
    else:
        add(missing("attestation.py 6"))
    add("")

    # -- 8. collusion and spot checks ------------------------------------- #
    add("## 8. Collusion, and the checks that catch it")
    add("")
    col = load("collusion.json")
    if col:
        add("A cartel abstains together, so no single refusal is ever contradicted.")
        add("")
        add("| Cartel size | Success |")
        add("|---|---|")
        for size, a in col["attack"].items():
            add(f"| {size} | {pct(a['success_rate'])} |")
        add("")
        add("Random spot checks (signed protocol):")
        add("")
        add("| Spot-check rate | Honest network | 6 colluding | Checks | Cartel overruled | False accusations |")
        add("|---|---|---|---|---|---|")
        for rate, r in col["defence"].items():
            add(f"| {float(rate):.0%} | {pct(r['honest']['success_rate'])} | "
                f"{pct(r['colluding']['success_rate'])} | {r['colluding']['spot_checks']:.0f} | "
                f"{r['colluding']['cartel_overruled']:.1f} | "
                f"{r['colluding']['false_accusations'] + r['honest']['false_accusations']:.1f} |")
        headline["collusion"] = (
            f"A cartel of 6 cuts success to {pct(col['attack']['6']['success_rate'])}; "
            f"15% random spot checks bring it back to "
            f"{pct(col['defence']['0.15']['colluding']['success_rate'])}.")
    else:
        add(missing("collusion.py 6"))
    add("")
    rep = load("reputation.json")
    if rep:
        add("Risk-based spot checks (hospitals already caught are checked more often):")
        add("")
        add("| Arm | Honest network | 6 colluding | Checks spent | Cartel overruled | False accusations |")
        add("|---|---|---|---|---|---|")
        for label, a in rep["arms"].items():
            add(f"| {label} | {pct(a['honest']['success_rate'])} | {pct(a['cartel']['success_rate'])} | "
                f"{a['cartel']['spot_checks']:.0f} | {a['cartel']['cartel_overruled']:.1f} | "
                f"{a['cartel']['false_accusations'] + a['honest']['false_accusations']:.1f} |")
        add("")
        for label in ("5%, 50% once caught", "5%, 100% once caught"):
            v = rep["arms"][label].get("vs_uniform_15")
            if v:
                add(f"- **{label}** vs uniform 15%: {pts(v['diff'])} ({pval(v['p'])}), using "
                    f"{v['checks_ratio']:.0%} of the checks.")
        v = rep["arms"]["5%, 50% once caught"].get("vs_uniform_15")
        if v:
            verdict = ("better" if v["diff"] > 0 and v["p"] < 0.05 else
                       "no better" if v["p"] >= 0.05 else "worse")
            headline["reputation"] = (
                f"Checking hospitals already caught more often is {verdict} than uniform "
                f"15% spot checks at similar effort ({pts(v['diff'])}, {pval(v['p'])}, "
                f"{v['checks_ratio']:.0%} of the checks).")
    else:
        add(missing("reputation.py 6"))
    add("")

    # -- 9. replication --------------------------------------------------- #
    add("## 9. Replication: Phoenix, 30 real hospitals, nothing re-tuned")
    add("")
    rp = load("replication_phoenix.json")
    if rp:
        add("| Week | MAHROS | Phone tree | Shared registry | Central greedy | MAHROS minus phone |")
        add("|---|---|---|---|---|---|")
        for wk, r in rp["coordination"].items():
            s = r["success"]
            add(f"| `{wk}` ({rp['meta']['weeks'][wk]}) | {pct(s['mahros'])} | {pct(s['phone'])} | "
                f"{pct(s['nearest'])} | {pct(s['central'])} | "
                f"{pts(r['mahros_minus_phone']['diff'])} ({pval(r['mahros_minus_phone']['p'])}) |")
        add("")
        add("| Phoenix surge week, 50% protective | Success | False accusations |")
        add("|---|---|---|")
        for label, a in rp["verification"]["arms"].items():
            add(f"| {label} | {pct(a['success'])} | {a['false_accusations']:.1f} |")
        vg = rp["verification"]
        add("")
        rec = vg.get("loss_recovered")
        add(f"Checking ON minus OFF: {pts(vg['checking_gain']['diff'])} "
            f"({pval(vg['checking_gain']['p'])})"
            + (f"; recovers {rec:.0%} of the loss." if rec is not None else "."))
        gaps = [r["mahros_minus_phone"]["diff"] for r in rp["coordination"].values()]
        headline["replication"] = (
            f"Phoenix (30 hospitals, not re-tuned): MAHROS beats the phone tree by "
            f"{min(gaps) * 100:+.1f} to {max(gaps) * 100:+.1f} points across four real weeks.")
    else:
        add(missing("replication.py 5"))
    add("")

    # -- 10. sensitivity --------------------------------------------------- #
    add("## 10. Sensitivity: does the ranking survive every unsourced value?")
    add("")
    sens = load("sensitivity.json")
    if sens:
        flips = sens.get("ranking_flips", [])
        add("**Every ranking survived every sweep.**" if not flips else
            "**Rankings that did not survive** (these claims are demoted):")
        for f in flips:
            add(f"- {f}")
        add("")
        for name, sweep in sens["sweeps"].items():
            add(f"<details><summary>{name.replace('_', ' ')}</summary>")
            add("")
            add("```")
            add(json.dumps(sweep, indent=1)[:2500])
            add("```")
            add("</details>")
            add("")
        phone_flips = [f for f in flips if "argumentation" not in f]
        adv_s = sens["sweeps"].get("adversary_strength", {})
        gains = {k: v["on"] - v["off"] for k, v in adv_s.items()}
        consistent = [g for k, g in gains.items() if k.endswith("p1.0")]
        half = [g for k, g in gains.items() if k.endswith("p0.5")]
        text = ("MAHROS beat the phone tree under every value of every unsourced parameter."
                if not phone_flips else
                f"MAHROS lost to the phone tree under: {', '.join(phone_flips)}.")
        if consistent and half:
            lo, hi = min(consistent) * 100, max(consistent) * 100
            gain = f"{lo:.1f}" if abs(hi - lo) < 0.05 else f"{lo:.1f} to {hi:.1f}"
            text += (f" Checking refusals adds {gain} points when protective hospitals lie "
                     f"consistently; when they lie only half the time a second round of "
                     f"asking already recovers almost everything, and checking costs up to "
                     f"{abs(min(half)) * 100:.1f} points.")
        headline["sensitivity"] = text
        add("")
        add(f"**In words:** {text}")
    else:
        add(missing("sensitivity.py 5"))
    add("")

    # -- 11. latency -------------------------------------------------------- #
    add("## 11. Decision time for one transfer (the protocol, not a deployment)")
    add("")
    rt = load("realtime.json")
    if rt:
        add("| Configuration | p50 | p95 | p99 |")
        add("|---|---|---|---|")
        for r in rt["arms"]:
            add(f"| {r['label']} | {r['p50_ms']:.2f} ms | {r['p95_ms']:.2f} ms | {r['p99_ms']:.2f} ms |")
        add("")
        add("Single process, in memory, no network: shows the protocol is not the "
            "bottleneck, not that a deployment would be fast.")
    else:
        add(missing("realtime.py"))
    add("")

    OUT.write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")

    # -- the generated blocks in README and the panel guide ----------------- #
    order = ["phone", "central", "registry_honest", "registry_attack", "adversarial",
             "ablation", "attestation", "collusion", "reputation", "replication",
             "validation", "integrity", "staff", "sensitivity"]
    bullets = [f"- {headline[k]}" for k in order if k in headline]
    if not bullets:
        bullets = ["*(no results built yet)*"]
    block = "\n".join(bullets) + "\n\nFull tables, and every caveat: [docs/RESULTS.md](docs/RESULTS.md)."
    _replace_block(ROOT / "README.md", "results", block)
    panel = "\n".join(bullets).replace("docs/RESULTS.md", "RESULTS.md")
    _replace_block(ROOT / "docs" / "PANEL_GUIDE.md", "results:panel", panel, end="results")
    print(f"updated README.md and docs/PANEL_GUIDE.md ({len(bullets)} headline lines)")
    return 0


def _replace_block(path: Path, start: str, body: str, end: str | None = None) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"(<!-- {re.escape(start)}:start -->\n).*?(\n<!-- {re.escape(end or start)}:end -->)",
                         re.S)
    if not pattern.search(text):
        raise SystemExit(f"{path.name}: marker <!-- {start}:start --> not found")
    path.write_text(pattern.sub(lambda m: m.group(1) + body + m.group(2), text), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
