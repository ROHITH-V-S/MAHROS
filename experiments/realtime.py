"""Is this fast enough to run live? Measured, not asserted.

    python experiments/realtime.py
    python experiments/realtime.py houston_surge 2000

Everything else in this repository is a batch simulation over a two-week
horizon. That answers "does the mechanism work" and says nothing at all about
"could it run in a hospital". This script answers the second question, because
a negotiation protocol for a deteriorating patient that needs four seconds to
decide is a research artefact, not a system.

What is measured
----------------
Wall-clock time to resolve **one** transfer request end to end: announce, bid,
deliberate (challenge / defence / attestation), score, award. Reported as
percentiles, because a mean hides exactly the tail that matters clinically.

The budget
----------
The clinical safe window for the sickest patients is 90 minutes, and road
transport eats most of it. Decision latency has to disappear inside the noise of
a phone call -- call it **one second** at p99 to be comfortably invisible, when
the human alternative is a coordinator dialling hospitals for 68 minutes.

What this is not
----------------
Single-process, in-memory, no network, no TLS, no database. A real deployment
adds a round trip per hospital and persistence for the ledger. Those costs are
real and are not measured here. What this establishes is that the *protocol
itself* -- the bidding, the argumentation, the signature verification -- is
nowhere near the budget, so the engineering problem is ordinary distributed
systems work rather than an algorithmic obstacle.
"""

from __future__ import annotations

import copy
import json
import statistics as st
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mahros.negotiation.attestation import (  # noqa: E402
    AttestationAuthority,
    Keyring,
    signer_backend,
)
from mahros.sim.runner import RunConfig, SimulationRunner  # noqa: E402
from mahros.sim.scenario import SCENARIOS  # noqa: E402

RESULTS = ROOT / "results"

#: p99 decision latency we regard as "invisible next to a phone call".
BUDGET_MS = 1000.0


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def instrumented_run(scenario: str, seed: int, days: float, **kw):
    """Run a simulation with a stopwatch around every single negotiation."""
    sc = copy.deepcopy(SCENARIOS[scenario])
    sc.seed = seed
    sc.horizon_hours = 24 * days
    runner = SimulationRunner(RunConfig(scenario=sc, strategy="mahros",
                                        seed=seed, **kw))

    latencies: list[float] = []
    strategy = runner.strategy
    original = strategy.resolve

    def timed(req, now):
        t0 = time.perf_counter()
        out = original(req, now)
        latencies.append((time.perf_counter() - t0) * 1000.0)
        return out

    strategy.resolve = timed
    result = runner.run()
    return result, latencies


def report(label: str, latencies: list[float]) -> dict:
    row = {
        "label": label,
        "n": len(latencies),
        "mean_ms": st.fmean(latencies),
        "p50_ms": _pct(latencies, 0.50),
        "p95_ms": _pct(latencies, 0.95),
        "p99_ms": _pct(latencies, 0.99),
        "max_ms": max(latencies),
        "within_budget": _pct(latencies, 0.99) < BUDGET_MS,
    }
    print(f"  {label:<34}{row['n']:>7}{row['mean_ms']:>9.2f}{row['p50_ms']:>9.2f}"
          f"{row['p95_ms']:>9.2f}{row['p99_ms']:>9.2f}{row['max_ms']:>10.2f}"
          f"{'  OK' if row['within_budget'] else '  OVER':>8}")
    return row


def signature_microbenchmark(n: int = 20000) -> dict:
    """Cost of the operation that has to sit inside a live negotiation."""
    auth = AttestationAuthority(Keyring(seed=b"bench"))
    t0 = time.perf_counter()
    atts = [auth.attest("450068", f"req-{i}", "icu_bed", float(i))
            for i in range(n)]
    sign_us = (time.perf_counter() - t0) / n * 1e6

    t0 = time.perf_counter()
    for att in atts:
        auth.verify(att)
    verify_us = (time.perf_counter() - t0) / n * 1e6

    print(f"\n  signature backend       : {signer_backend()}")
    print(f"  co-sign one attestation : {sign_us:8.1f} us")
    print(f"  verify one attestation  : {verify_us:8.1f} us")
    print(f"  -> verification is local and offline: no authority round trip,")
    print(f"     which is what keeps it inside the clinical window.")
    return {"backend": signer_backend(), "sign_us": sign_us,
            "verify_us": verify_us, "n": n}


def main(scenario: str = "houston_surge", days: float = 4) -> int:
    seeds = [42, 143, 244]
    print(f"\nDECISION LATENCY -- one transfer request, end to end")
    print(f"scenario={scenario}  27 real hospitals  {len(seeds)} seeds  "
          f"{days:.0f}-day horizon")
    print(f"budget: p99 < {BUDGET_MS:.0f} ms\n")
    print(f"  {'configuration':<34}{'n':>7}{'mean':>9}{'p50':>9}{'p95':>9}"
          f"{'p99':>9}{'max':>10}{'':>8}")
    print(f"  {'':<34}{'':>7}{'ms':>9}{'ms':>9}{'ms':>9}{'ms':>9}{'ms':>10}")

    arms = [
        ("plain contract net", dict(enable_argumentation=False)),
        ("+ ledger-backed challenge", {}),
        ("+ attestation (deterrence .8)",
         dict(enable_attestation=True, attestation_deterrence=0.8)),
        ("adversarial, 50% strategic",
         dict(strategic_fraction=0.5, enable_attestation=True,
              attestation_deterrence=0.8)),
    ]

    rows = []
    for label, kw in arms:
        pooled: list[float] = []
        for seed in seeds:
            _, lat = instrumented_run(scenario, seed, days, **kw)
            pooled.extend(lat)
        rows.append(report(label, pooled))

    bench = signature_microbenchmark()

    worst = max(r["p99_ms"] for r in rows)
    print(f"\n  {'-' * 76}")
    print(f"  worst p99 across every configuration: {worst:.2f} ms "
          f"({BUDGET_MS / worst:.0f}x inside the {BUDGET_MS:.0f} ms budget)")
    print(f"  for comparison, the phone tree spends 68.3 MINUTES of coordinator")
    print(f"  time per transfer -- about {68.3 * 60_000 / worst:,.0f}x this.")
    print(f"\n  Caveat, stated rather than buried: single process, in memory, no")
    print(f"  network, no TLS, no persistence. A deployment adds a round trip per")
    print(f"  hospital and a durable ledger write. This shows the protocol is not")
    print(f"  the bottleneck; it does not show a deployment would be.")

    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / "realtime.json"
    path.write_text(json.dumps({
        "scenario": scenario, "seeds": seeds, "horizon_days": days,
        "budget_ms": BUDGET_MS, "arms": rows, "signatures": bench,
    }, indent=2, default=float), encoding="utf-8")
    print(f"\n  wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "houston_surge"
    d = float(sys.argv[2]) if len(sys.argv) > 2 else 4
    raise SystemExit(main(name, d))
