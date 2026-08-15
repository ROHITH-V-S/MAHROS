"""MAHROS command line.

    python -m mahros.cli run       --scenario surge --strategy mahros
    python -m mahros.cli compare   --scenario surge --seeds 5
    python -m mahros.cli ablation  --scenario surge_scarcity --seeds 5
    python -m mahros.cli explain   --scenario surge --n 5
    python -m mahros.cli verify    --scenario baseline
    python -m mahros.cli doctor
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .sim import metrics as M
from .sim.runner import RunConfig, SimulationRunner
from .sim.scenario import SCENARIOS
from .sim.strategies import STRATEGIES

app = typer.Typer(add_completion=False, help="MAHROS simulation harness")
console = Console()
RESULTS = Path(__file__).resolve().parent.parent / "results"


def _run(scenario: str, strategy: str, seed: int, **kw) -> M.Metrics:
    sc = SCENARIOS[scenario]
    import copy
    sc = copy.deepcopy(sc)
    sc.seed = seed
    cfg = RunConfig(scenario=sc, strategy=strategy, seed=seed, **kw)
    return M.compute(SimulationRunner(cfg).run())


# --------------------------------------------------------------------------- #

@app.command()
def run(
    scenario: str = typer.Option("baseline", help=f"one of {list(SCENARIOS)}"),
    strategy: str = typer.Option("mahros", help=f"one of {list(STRATEGIES)}"),
    seed: int = 42,
    llm: bool = typer.Option(False, help="enable the LLM coordinator"),
    arbitrate: bool = typer.Option(False, help="let the LLM pick winners (ablation)"),
    no_fairness: bool = typer.Option(False, help="disable the fairness layer (ablation)"),
    presidio: bool = typer.Option(False, help="use Presidio if installed"),
):
    """Run a single simulation and print the full metric set."""
    m = _run(
        scenario, strategy, seed,
        llm_enabled=llm, llm_arbitration=arbitrate,
        fairness_enabled=not no_fairness, use_presidio=presidio,
    )
    _print_metrics(m)
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"run_{scenario}_{strategy}_{seed}.json"
    out.write_text(json.dumps(m.row(), indent=2, default=str), encoding="utf-8")
    console.print(f"\n[dim]written to {out}[/dim]")


@app.command()
def compare(
    scenario: str = typer.Option("surge"),
    seeds: int = typer.Option(5, help="number of random seeds per arm"),
    strategies: str = typer.Option("mahros,phone,central,nearest,none"),
    llm: bool = False,
):
    """Run every strategy across N seeds and print the comparison table."""
    arms = [s.strip() for s in strategies.split(",")]
    agg: dict[str, dict] = {}
    for arm in arms:
        runs = []
        with console.status(f"[cyan]{arm}[/cyan] x{seeds} seeds on {scenario}..."):
            for s in range(seeds):
                runs.append(_run(scenario, arm, 42 + s * 101, llm_enabled=llm))
        agg[arm] = M.aggregate(runs)
        console.print(f"  [green]done[/green] {arm}")

    _print_comparison(agg, scenario)

    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"compare_{scenario}_{seeds}seeds.json"
    out.write_text(json.dumps(agg, indent=2, default=str), encoding="utf-8")

    if "phone" in agg and "mahros" in agg:
        console.print("\n[bold]MAHROS vs today's practice (PhoneTree)[/bold]")
        _print_delta(M.compare(agg["phone"], agg["mahros"]))
    if "central" in agg and "mahros" in agg:
        console.print("\n[bold]MAHROS vs omniscient central authority[/bold]")
        console.print("[dim]Central sees all private data; MAHROS sees none. "
                      "Closing most of this gap is the contribution.[/dim]")
        _print_delta(M.compare(agg["central"], agg["mahros"]))
    console.print(f"\n[dim]written to {out}[/dim]")


@app.command()
def ablation(
    scenario: str = typer.Option("surge_scarcity"),
    seeds: int = typer.Option(5),
):
    """Which MAHROS component actually earns its place?"""
    arms = {
        "full": {},
        "no_fairness": {"fairness_enabled": False},
        "no_audit": {"audit_enabled": False},
        "llm_explain": {"llm_enabled": True},
        "llm_arbitrate": {"llm_enabled": True, "llm_arbitration": True},
    }
    agg = {}
    for label, kw in arms.items():
        runs = []
        with console.status(f"[cyan]{label}[/cyan] x{seeds}..."):
            for s in range(seeds):
                runs.append(_run(scenario, "mahros", 42 + s * 101, **kw))
        agg[label] = M.aggregate(runs)
        console.print(f"  [green]done[/green] {label}")
    _print_comparison(agg, f"{scenario} (ablation)")
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"ablation_{scenario}.json").write_text(
        json.dumps(agg, indent=2, default=str), encoding="utf-8"
    )


@app.command()
def explain(
    scenario: str = typer.Option("surge"),
    n: int = typer.Option(5, help="how many decisions to show"),
    llm: bool = typer.Option(False, help="use a live LLM instead of the template"),
):
    """Show the plain-English rationale attached to real negotiated decisions."""
    import copy
    sc = copy.deepcopy(SCENARIOS[scenario])
    sc.horizon_hours = min(sc.horizon_hours, 24 * 3)
    cfg = RunConfig(scenario=sc, strategy="mahros", seed=42, llm_enabled=llm)
    result = SimulationRunner(cfg).run()

    shown = 0
    for a in result.agreements:
        if not a.rationale:
            continue
        console.print(
            Panel(
                a.rationale,
                title=f"[bold]{a.origin} -> {a.receiver}[/bold]  "
                      f"({a.resource.value}, decided by {a.decided_by})",
                subtitle=f"terms hash {a.terms_hash[:16]}...",
                border_style="cyan",
            )
        )
        shown += 1
        if shown >= n:
            break
    if result.coordinator:
        console.print(result.coordinator.stats())


@app.command()
def verify(scenario: str = typer.Option("baseline"), seed: int = 42):
    """Prove the audit trail is tamper-evident by trying to forge it."""
    import copy
    sc = copy.deepcopy(SCENARIOS[scenario])
    sc.horizon_hours = min(sc.horizon_hours, 24 * 3)
    result = SimulationRunner(RunConfig(scenario=sc, strategy="mahros", seed=seed)).run()
    ledger = result.ledger

    before = ledger.verify()
    console.print(f"[green]intact[/green]  {before}")

    # Simulate a hospital quietly under-reporting the transfers it accepted.
    tampered = False
    for block in ledger.chain[1:]:
        if block.entries:
            block.entries[0]["receiver"] = "H99_FORGED"
            tampered = True
            break
    if not tampered:
        console.print("[yellow]not enough agreements to tamper with; "
                      "try a longer scenario[/yellow]")
        return

    after = ledger.verify()
    console.print(f"[red]after tampering[/red]  {after}")
    if not after["valid"]:
        console.print(Panel(
            "Tampering detected. A hospital cannot silently rewrite what it "
            "agreed to, so the fairness counts derived from this ledger cannot "
            "be gamed by self-reporting.",
            border_style="green", title="Audit property holds",
        ))


@app.command()
def doctor():
    """Check which optional layers are available on this machine."""
    table = Table(title="MAHROS environment")
    table.add_column("component"); table.add_column("status"); table.add_column("note")

    def probe(mod: str):
        try:
            __import__(mod); return True
        except Exception:
            return False

    core_ok = probe("numpy") and probe("typer")
    table.add_row("core simulation", "[green]ready[/green]" if core_ok else "[red]missing[/red]",
                  "stdlib + numpy/typer/rich")
    table.add_row("Presidio (privacy)",
                  "[green]installed[/green]" if probe("presidio_analyzer") else "[yellow]fallback[/yellow]",
                  "HMAC tokenizer + regex used if absent")
    table.add_row("web3 (EVM ledger)",
                  "[green]installed[/green]" if probe("web3") else "[yellow]fallback[/yellow]",
                  "hash-chain ledger used if absent")
    table.add_row("NegMAS (negotiation)",
                  "[green]installed[/green]" if probe("negmas") else "[yellow]not used[/yellow]",
                  "built-in Contract Net used by default")

    provider = os.getenv("MAHROS_LLM_PROVIDER", "none")
    if provider == "none":
        llm_status, note = "[yellow]template[/yellow]", "set MAHROS_LLM_PROVIDER=ollama|groq|gemini"
    elif provider == "ollama":
        llm_status, note = "[green]ollama[/green]", "local, free; ensure `ollama serve` is running"
    else:
        key = os.getenv(f"{provider.upper()}_API_KEY")
        llm_status = "[green]" + provider + "[/green]" if key else "[red]no key[/red]"
        note = f"needs {provider.upper()}_API_KEY"
    table.add_row("LLM coordinator", llm_status, note)
    console.print(table)


# --------------------------------------------------------------------------- #

def _print_metrics(m: M.Metrics) -> None:
    t = Table(title=f"{m.strategy} on {m.scenario} (seed {m.seed})", show_header=True)
    t.add_column("metric"); t.add_column("value", justify="right"); t.add_column("meaning")
    rows = [
        ("transfer requests", f"{m.n_requests}", "escalations needing another hospital"),
        ("success rate", f"{m.success_rate:.1%}", "reached definitive care in time"),
        ("mean wait (min)", f"{m.mean_wait:.1f}", "escalation -> care started"),
        ("p90 wait (min)", f"{m.p90_wait:.1f}", "the tail is what harms patients"),
        ("breach rate", f"{m.breach_rate:.1%}", "missed the clinical safe window"),
        ("critical breach rate", f"{m.critical_breach_rate:.1%}", "acuity 4-5 only"),
        ("burden Gini", f"{m.burden_gini:.3f}", "0 = load perfectly shared"),
        ("burden Jain", f"{m.burden_jain:.3f}", "1 = perfectly fair"),
        ("wait equity gap (min)", f"{m.wait_equity_gap:.1f}", "best vs worst-served group"),
        ("mean ICU utilisation", f"{m.mean_icu_utilisation:.1%}", "capacity actually used"),
        ("coordinator min/transfer", f"{m.coordinator_minutes_per_transfer:.1f}", "human staff cost"),
        ("messages/transfer", f"{m.messages_per_transfer:.1f}", "communication overhead"),
        ("ledger valid", str(m.ledger_valid), f"{m.ledger_records} agreements recorded"),
        ("privacy violations", str(m.privacy_violations), "identifiers crossing a boundary"),
        ("decentralised", str(m.decentralised), f"max {m.max_peers_disclosed} peers seen by one node"),
    ]
    for r in rows:
        t.add_row(*r)
    console.print(t)


def _print_comparison(agg: dict[str, dict], scenario: str) -> None:
    t = Table(title=f"scenario: {scenario}")
    t.add_column("metric")
    for arm in agg:
        t.add_column(arm, justify="right")
    labels = [
        ("success_rate", "success rate", "{:.1%}"),
        ("mean_wait", "mean wait (min)", "{:.1f}"),
        ("p90_wait", "p90 wait (min)", "{:.1f}"),
        ("breach_rate", "breach rate", "{:.1%}"),
        ("critical_breach_rate", "critical breach", "{:.1%}"),
        ("burden_gini", "burden Gini", "{:.3f}"),
        ("burden_jain", "burden Jain", "{:.3f}"),
        ("wait_equity_gap", "equity gap (min)", "{:.1f}"),
        ("mean_icu_utilisation", "ICU utilisation", "{:.1%}"),
        ("coordinator_minutes_per_transfer", "staff min/transfer", "{:.1f}"),
        ("messages_per_transfer", "messages/transfer", "{:.1f}"),
    ]
    for key, label, fmt in labels:
        row = [label]
        for arm in agg:
            v = agg[arm].get(key)
            ci = agg[arm].get(f"{key}_ci95", 0.0)
            row.append(f"{fmt.format(v)} ±{fmt.format(ci).lstrip('0') if ci else '0'}"
                       if v is not None else "-")
        t.add_row(*row)
    console.print(t)


def _print_delta(delta: dict) -> None:
    t = Table()
    t.add_column("metric"); t.add_column("baseline", justify="right")
    t.add_column("MAHROS", justify="right"); t.add_column("change", justify="right")
    for k, v in delta.items():
        colour = "green" if v["better"] else "red"
        t.add_row(k, f"{v['baseline']:.3f}", f"{v['treatment']:.3f}",
                  f"[{colour}]{v['pct_change']:+.1f}%[/{colour}]")
    console.print(t)


if __name__ == "__main__":
    app()
