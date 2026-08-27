"""Optimal assignment (Hungarian / Jonker-Volgenant), pure standard library.

Why this is here
----------------
The original "centralized (omniscient)" baseline was not an optimiser. It read
every hospital's state, then picked the best hospital for **one request at a
time** using the same scoring function MAHROS uses. That makes the headline
finding -- "MAHROS matches the centralised optimiser" -- close to circular: the
two arms differed only by a fairness term and a one-minute latency constant, so
of course they matched.

A real centralised optimiser sees several pending transfers at once and solves
them **jointly**, which is strictly stronger. Two patients both want the one
free bed at the nearest tertiary centre; a greedy per-request optimiser gives it
to whichever asks first and sends the other a long way, while a joint solver
sees that swapping them saves twenty minutes overall. That is the ceiling
MAHROS should be measured against, and closing most of the gap to it *without*
anybody pooling their data is a result worth reporting.

This module gives the joint solve. `mahros/sim/strategies.py` uses it for the
`optimal` arm.

Implementation
--------------
The O(n^3) shortest-augmenting-path form of the Hungarian algorithm with dual
potentials, for rectangular cost matrices with rows <= columns. Validated
against `scipy.optimize.linear_sum_assignment` in `tests/test_assignment.py`
when scipy is present.
"""

from __future__ import annotations

from typing import Sequence

INF = float("inf")


def linear_sum_assignment(cost: Sequence[Sequence[float]]) -> list[int]:
    """Minimum-cost assignment of every row to a distinct column.

    Returns a list `out` where `out[i]` is the column assigned to row i, or -1
    if row i could not be assigned. Requires rows <= columns; pad with dummy
    columns of equal high cost if that does not hold.

    Costs of `inf` mark forbidden pairings (this hospital cannot take this
    patient at all) and are never selected unless nothing else exists.
    """
    n = len(cost)
    if n == 0:
        return []
    m = len(cost[0])
    if m < n:
        raise ValueError(f"need columns >= rows, got {m} < {n}")

    # Potentials and the current column->row matching. Index 0 is a sentinel,
    # which is what keeps the augmenting-path bookkeeping branch-free.
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    match = [0] * (m + 1)          # match[j] = row assigned to column j
    way = [0] * (m + 1)            # predecessor column on the augmenting path

    big = 1e18                     # stand-in for inf during arithmetic

    def c(i: int, j: int) -> float:
        val = cost[i][j]
        return big if val == INF else float(val)

    for row in range(1, n + 1):
        match[0] = row
        j0 = 0
        minv = [INF] * (m + 1)
        used = [False] * (m + 1)

        while True:
            used[j0] = True
            i0 = match[j0]
            delta = INF
            j1 = -1
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cur = c(i0 - 1, j - 1) - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            if j1 == -1:
                break                       # no reachable column: row unassigned
            for j in range(m + 1):
                if used[j]:
                    u[match[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if match[j0] == 0:
                break

        # Walk the augmenting path back, flipping the matching as we go.
        while j0:
            j1 = way[j0]
            match[j0] = match[j1]
            j0 = j1

    out = [-1] * n
    for j in range(1, m + 1):
        if match[j] > 0:
            out[match[j] - 1] = j - 1
    return out


def total_cost(cost: Sequence[Sequence[float]], assignment: Sequence[int]) -> float:
    """Sum of the assigned costs. Unassigned rows contribute nothing."""
    return sum(float(cost[i][j]) for i, j in enumerate(assignment)
               if j >= 0 and cost[i][j] != INF)
