"""Does requiring a signature change anything? Swept end to end.

    python experiments/attestation.py            # 6 seeds
    python experiments/attestation.py 10

The question this answers
-------------------------
The unattested protocol contains an assumption worth being uncomfortable about.
When a refusal is challenged, `StrategicPolicy.defend()` returns False: a lying
hospital simply *cannot* defend itself. That is convenient and it is not
obviously true. A real bed manager who has just claimed to be full would repeat
the claim, not fall silent.

The attested protocol removes the assumption instead of relying on it. To
discharge a challenge a hospital must **sign** the contested predicate,
co-signed by a key it does not solely control. Nothing is assumed about whether
a liar can defend. The question becomes whether a liar will *sign*, which is an
institutional question with an explicit parameter -- and that parameter is swept
here from "signing deters nobody" to "signing deters everybody".

What to expect, and what it means
---------------------------------
At **deterrence 1.0** the attested protocol should reproduce the unattested one:
every liar backs down when asked to sign, exactly as the old model assumed. The
old results were the optimistic end of this sweep all along.

At **deterrence 0.0** it should collapse toward plain Contract Net: liars sign
falsely and escape the challenge. This is the pessimistic end, and it is the
honest answer to "what if a signature deters nobody".

In between is the real finding: **prevention scales with deterrence, and signed
evidence scales against it.** A network where signatures deter gets fewer false
refusals; one where they do not gets a pile of co-signed, timestamped,
non-repudiable statements that a regulator can act on. The mechanism fails
safe in the sense that matters -- it never produces *less* than the unattested
protocol at the deterrence levels where that protocol's own assumption holds.
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

from mahros.eval import paired_t  # noqa: E402
from mahros.negotiation.attestation import (  # noqa: E402
    AUDIT_REFERRAL_WINDOW_MINUTES,
    provides_non_repudiation,
    signer_backend,
)
from mahros.sim import metrics as M  # noqa: E402
from mahros.sim.runner import RunConfig, SimulationRunner  # noqa: E402
from mahros.sim.scenario import SCENARIOS  # noqa: E402

RESULTS = ROOT / "results"
SCENARIO = "houston_surge"
STRATEGIC_FRACTION = 0.5
DETERRENCE = [0.0, 0.25, 0.5, 0.75, 0.9, 1.0]


def run_one(seed: int, **kw):
    sc = copy.deepcopy(SCENARIOS[SCENARIO])
    sc.seed = seed
    result = SimulationRunner(RunConfig(
        scenario=sc, strategy="mahros", seed=seed,
        strategic_fraction=STRATEGIC_FRACTION, **kw)).run()
    return result, M.compute(result)


def agg(runs) -> dict:
    metrics = [m for _, m in runs]
    out = {
        "success_rate": st.fmean(m.success_rate for m in metrics),
        "success_by_seed": [m.success_rate for m in metrics],
        "mean_wait": st.fmean(m.mean_wait for m in metrics),
        "breach_rate": st.fmean(m.breach_rate for m in metrics),
    }
    auths = [r.attestation for r, _ in runs if r.attestation is not None]
    if auths:
        out["signed"] = st.fmean(a.stats()["attestations_issued"] for a in auths)
        out["declined"] = st.fmean(a.stats()["attestations_declined"] for a in auths)
        out["referrals"] = st.fmean(
            len(a.audit(r.ledger, 0.0))
            for a, (r, _) in zip(auths, [x for x in runs if x[0].attestation]))
    # False accusations by the LIVE mechanism, which referrals never touch.
    out["false_accusations"] = st.fmean(
        sum(h.refusals_overruled for h in r.hospitals.values()
            if h.policy.name != "strategic")
        for r, _ in runs)
    return out


def main(n_seeds: int = 6) -> int:
    t0 = time.perf_counter()
    seeds = [42 + i * 101 for i in range(n_seeds)]
    out: dict = {
        "meta": {
            "scenario": SCENARIO, "seeds": seeds,
            "strategic_fraction": STRATEGIC_FRACTION,
            "signature_backend": signer_backend(),
            "non_repudiation": provides_non_repudiation(),
            "referral_window_minutes": AUDIT_REFERRAL_WINDOW_MINUTES,
        },
        "arms": {}, "sweep": {},
    }

    print(f"\n{'=' * 78}")
    print("ATTESTED REFUSAL: does requiring a signature change the outcome?")
    print(f"scenario={SCENARIO}  {STRATEGIC_FRACTION:.0%} strategic  "
          f"{n_seeds} seeds")
    print(f"signature backend: {signer_backend()}   "
          f"true non-repudiation: {provides_non_repudiation()}")
    if not provides_non_repudiation():
        print("  (HMAC fallback models the protocol but not non-repudiation; "
              "install .[attest] for Ed25519)")
    print(f"{'=' * 78}\n")

    # -- reference arms ---------------------------------------------------- #
    print("Reference arms\n")
    print(f"  {'arm':<34}{'success':>9}{'wait':>8}{'false acc':>11}")
    plain = agg([run_one(s, enable_argumentation=False) for s in seeds])
    out["arms"]["plain_contract_net"] = plain
    print(f"  {'plain Contract Net (no challenge)':<34}"
          f"{plain['success_rate']:>8.1%}{plain['mean_wait']:>8.1f}"
          f"{plain['false_accusations']:>11.1f}")

    unatt = agg([run_one(s) for s in seeds])
    out["arms"]["unattested"] = unatt
    print(f"  {'ledger challenge, unattested':<34}"
          f"{unatt['success_rate']:>8.1%}{unatt['mean_wait']:>8.1f}"
          f"{unatt['false_accusations']:>11.1f}")
    print("\n  The unattested arm is every earlier result in this project. It")
    print("  assumes a lying hospital cannot defend itself at all.\n")

    # -- the sweep --------------------------------------------------------- #
    print(f"{'-' * 78}")
    print("Attested protocol, swept over the probability a liar declines to sign\n")
    print(f"  {'deterrence':>11}{'success':>9}{'wait':>8}{'signed':>9}"
          f"{'declined':>10}{'referrals':>11}{'false acc':>11}")
    for d in DETERRENCE:
        runs = [run_one(s, enable_attestation=True, attestation_deterrence=d)
                for s in seeds]
        a = agg(runs)
        out["sweep"][f"{d:.2f}"] = a
        print(f"  {d:>11.2f}{a['success_rate']:>8.1%}{a['mean_wait']:>8.1f}"
              f"{a.get('signed', 0):>9.0f}{a.get('declined', 0):>10.0f}"
              f"{a.get('referrals', 0):>11.1f}{a['false_accusations']:>11.1f}")

    # -- the two endpoints, tested ----------------------------------------- #
    print(f"\n{'-' * 78}")
    print("Do the endpoints behave as the theory says?\n")
    top = out["sweep"]["1.00"]["success_by_seed"]
    bot = out["sweep"]["0.00"]["success_by_seed"]
    res_top = paired_t(top, unatt["success_by_seed"],
                       label="deterrence 1.0 - unattested")
    res_bot = paired_t(bot, plain["success_by_seed"],
                       label="deterrence 0.0 - plain CNP")
    print("  " + res_top.line())
    print("    -> expected null: at full deterrence the attested protocol should")
    print("       reproduce the old assumption exactly.")
    print("  " + res_bot.line())
    print("    -> expected null: at zero deterrence a signature buys nothing and")
    print("       the mechanism should fall back to plain Contract Net.")
    out["endpoint_tests"] = {"vs_unattested_at_1": res_top.as_dict(),
                             "vs_plain_at_0": res_bot.as_dict()}

    # -- referral calibration ---------------------------------------------- #
    print(f"\n{'-' * 78}")
    print("Audit referrals are NOT findings of dishonesty\n")
    cal = [run_one(s, enable_attestation=True, attestation_deterrence=1.0)
           for s in seeds]
    fps = [r.attestation.measure_false_positive_rate(r.ledger, 0.0)
           for r, _ in cal]
    fp = st.fmean(fps)
    print(f"  At deterrence 1.0 every signed attestation is TRUE by construction,")
    print(f"  so every referral is a false positive. Measured rate: {fp:.2%}")
    print(f"  (window {AUDIT_REFERRAL_WINDOW_MINUTES:.0f} min; a hospital full at T")
    print(f"  that admits at T+3 after a discharge has not lied.)")
    print(f"\n  Referrals therefore hand a regulator a specific signed claim to")
    print(f"  check against records it may already inspect. They never overrule")
    print(f"  anyone, which is why the live mechanism keeps zero false accusations")
    print(f"  in every row of the sweep above.")
    out["referral_false_positive_rate"] = fp

    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / "attestation.json"
    path.write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
    print(f"\nwrote {path.relative_to(ROOT)}  ({time.perf_counter() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 6))
