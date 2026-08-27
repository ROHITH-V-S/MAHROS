"""Paired tests, equivalence tests, and bootstrap intervals. Pure stdlib.

Why hand-rolled rather than scipy
---------------------------------
The rest of MAHROS runs on the standard library so that a reviewer can execute
it with no environment to build. These are the four tests the paper needs and
they are short. `tests/test_stats.py` validates every one of them against
scipy when scipy happens to be installed, and skips otherwise -- so the
implementations are checked against a reference rather than trusted.

The one that matters most
-------------------------
`tost_equivalence` is the test for "these two systems perform the same". A
non-significant t-test does **not** show that. It shows you failed to detect a
difference, which is also what you get from a tiny sample or a noisy metric.
TOST inverts the burden of proof: it asks whether the difference is
*demonstrably smaller* than a margin you declared in advance. If MAHROS is to
claim it matches a centralised optimiser without centralising data, this is the
test that has to pass, and the margin has to be stated before looking.
"""

from __future__ import annotations

import math
import random
import statistics as st
from dataclasses import dataclass
from typing import Sequence


# --------------------------------------------------------------------------- #
# Student's t distribution, via the regularised incomplete beta function
# --------------------------------------------------------------------------- #

def _betacf(a: float, b: float, x: float, iterations: int = 200) -> float:
    """Continued-fraction expansion for the incomplete beta (Lentz's method)."""
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, iterations + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-12:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
             + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(lbeta) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbeta) * _betacf(b, a, 1.0 - x) / b


def t_sf(t: float, df: float) -> float:
    """P(T > t) for Student's t with `df` degrees of freedom (upper tail)."""
    if df <= 0:
        return float("nan")
    x = df / (df + t * t)
    tail = 0.5 * _betai(df / 2.0, 0.5, x)
    return tail if t > 0 else 1.0 - tail


def t_ppf(p: float, df: float) -> float:
    """Inverse CDF, by bisection. Fast enough: we call it a handful of times."""
    if not 0.0 < p < 1.0:
        return float("nan")
    lo, hi = -100.0, 100.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if (1.0 - t_sf(mid, df)) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# --------------------------------------------------------------------------- #
# Paired comparison
# --------------------------------------------------------------------------- #

@dataclass
class PairedResult:
    n: int
    mean_difference: float
    sd_difference: float
    stderr: float
    t: float
    df: int
    p_value: float
    ci_low: float
    ci_high: float
    cohens_dz: float
    label: str = ""

    @property
    def significant(self) -> bool:
        return self.p_value < 0.05

    def as_dict(self) -> dict:
        return {
            "label": self.label, "n": self.n,
            "mean_difference": round(self.mean_difference, 5),
            "ci95": [round(self.ci_low, 5), round(self.ci_high, 5)],
            "t": round(self.t, 4), "df": self.df,
            "p_value": round(self.p_value, 6),
            "cohens_dz": round(self.cohens_dz, 4),
            "significant_at_05": self.significant,
        }

    def line(self) -> str:
        star = "*" if self.significant else " "
        return (f"  {self.label:<34s} d={self.mean_difference:+.4f} "
                f"[{self.ci_low:+.4f}, {self.ci_high:+.4f}]  "
                f"t({self.df})={self.t:+.2f}  p={self.p_value:.4f}{star}  "
                f"dz={self.cohens_dz:+.2f}")


def paired_t(a: Sequence[float], b: Sequence[float], label: str = "") -> PairedResult:
    """Two-sided paired t-test on a-b, over common random numbers.

    Pairing matters here. Each seed produces a matched pair of runs on the same
    patient stream, so the seed-to-seed variance -- which is large, because a
    fortnight of a 12-hospital network is a noisy thing -- cancels out. An
    unpaired test on the same data would need several times the seeds for the
    same power.
    """
    diffs = [float(x) - float(y) for x, y in zip(a, b)]
    n = len(diffs)
    if n < 2:
        raise ValueError("paired_t needs at least two pairs")
    mean = st.fmean(diffs)
    sd = st.stdev(diffs)
    se = sd / math.sqrt(n) if sd > 0 else 1e-12
    t = mean / se
    df = n - 1
    p = 2.0 * t_sf(abs(t), df)
    crit = t_ppf(0.975, df)
    return PairedResult(
        n=n, mean_difference=mean, sd_difference=sd, stderr=se, t=t, df=df,
        p_value=p, ci_low=mean - crit * se, ci_high=mean + crit * se,
        cohens_dz=mean / sd if sd > 0 else 0.0, label=label,
    )


# --------------------------------------------------------------------------- #
# Equivalence (TOST)
# --------------------------------------------------------------------------- #

@dataclass
class EquivalenceResult:
    n: int
    mean_difference: float
    margin: float
    p_lower: float
    p_upper: float
    p_value: float                 # the larger of the two, per TOST
    ci_low: float
    ci_high: float
    equivalent: bool
    label: str = ""

    def as_dict(self) -> dict:
        return {
            "label": self.label, "n": self.n,
            "mean_difference": round(self.mean_difference, 5),
            "equivalence_margin": self.margin,
            "ci90": [round(self.ci_low, 5), round(self.ci_high, 5)],
            "p_value": round(self.p_value, 6),
            "equivalent_at_05": self.equivalent,
        }

    def line(self) -> str:
        verdict = "EQUIVALENT" if self.equivalent else "not shown"
        return (f"  {self.label:<34s} d={self.mean_difference:+.4f} "
                f"90% CI [{self.ci_low:+.4f}, {self.ci_high:+.4f}] "
                f"vs margin +/-{self.margin:.3f}  p={self.p_value:.4f}  -> {verdict}")


def tost_equivalence(
    a: Sequence[float],
    b: Sequence[float],
    margin: float,
    label: str = "",
) -> EquivalenceResult:
    """Two one-sided tests for equivalence of paired samples.

    `margin` is the largest difference you are willing to call "the same", and
    it must be chosen on clinical grounds *before* seeing the data. For
    transfer success rate we use 2 percentage points: a difference smaller than
    that would not change a commissioning decision.

    Declared equivalent when the 90% confidence interval on the difference lies
    entirely inside +/- margin. (90%, not 95% -- that is not a slip; it is what
    makes TOST a 5% test.)
    """
    diffs = [float(x) - float(y) for x, y in zip(a, b)]
    n = len(diffs)
    if n < 2:
        raise ValueError("tost_equivalence needs at least two pairs")
    mean = st.fmean(diffs)
    sd = st.stdev(diffs)
    se = sd / math.sqrt(n) if sd > 0 else 1e-12
    df = n - 1

    t_lower = (mean + margin) / se          # H0: difference <= -margin
    t_upper = (mean - margin) / se          # H0: difference >= +margin
    p_lower = t_sf(t_lower, df)
    p_upper = 1.0 - t_sf(t_upper, df)
    p = max(p_lower, p_upper)

    crit = t_ppf(0.95, df)                  # 90% two-sided interval
    lo, hi = mean - crit * se, mean + crit * se
    return EquivalenceResult(
        n=n, mean_difference=mean, margin=margin,
        p_lower=p_lower, p_upper=p_upper, p_value=p,
        ci_low=lo, ci_high=hi,
        equivalent=(lo > -margin and hi < margin), label=label,
    )


# --------------------------------------------------------------------------- #
# Bootstrap + multiple comparisons
# --------------------------------------------------------------------------- #

def bootstrap_ci(
    values: Sequence[float],
    statistic=st.fmean,
    confidence: float = 0.95,
    resamples: int = 10000,
    seed: int = 12345,
) -> tuple[float, float, float]:
    """Percentile bootstrap. Returns (point estimate, low, high).

    Used for metrics like Gini where the sampling distribution is skewed and a
    normal-theory interval would be misleading.
    """
    values = [float(v) for v in values]
    if len(values) < 2:
        v = values[0] if values else 0.0
        return v, v, v
    rng = random.Random(seed)
    n = len(values)
    stats = []
    for _ in range(resamples):
        stats.append(statistic([values[rng.randrange(n)] for _ in range(n)]))
    stats.sort()
    alpha = (1.0 - confidence) / 2.0
    lo = stats[int(alpha * resamples)]
    hi = stats[min(resamples - 1, int((1.0 - alpha) * resamples))]
    return statistic(values), lo, hi


def holm_adjust(p_values: dict[str, float]) -> dict[str, float]:
    """Holm-Bonferroni step-down correction.

    The paper reports several comparisons on the same runs. Without a
    correction, one of them being significant at p<0.05 means very little.
    Holm is uniformly more powerful than Bonferroni and needs no independence
    assumption, which we could not justify here anyway.
    """
    items = sorted(p_values.items(), key=lambda kv: kv[1])
    m = len(items)
    adjusted: dict[str, float] = {}
    running = 0.0
    for i, (key, p) in enumerate(items):
        val = min(1.0, (m - i) * p)
        running = max(running, val)          # enforce monotonicity
        adjusted[key] = running
    return adjusted
