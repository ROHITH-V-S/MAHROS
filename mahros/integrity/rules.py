"""Rules for spotting capacity reports that cannot be right, or look copied.

MAHROS's verification layer exists because a hospital's word about its own
capacity cannot simply be trusted. The simulation models that with invented
behaviour. This module looks at what hospitals *really* reported to HHS and
asks the same question of the real data.

Each rule is a plain sentence a reviewer can check by hand against the row:

  red    cannot be right as reported
         - more patients than staffed beds (inpatient or ICU)
         - more ICU beds than inpatient beds (ICU beds are part of inpatient)
         - zero inpatients for the week in a hospital with staffed beds, when
           the same hospital reports patients in other weeks
         - a bed count under half of what the same hospital reported in the
           surrounding weeks (a partial report standing in for the whole
           hospital). Compared with its neighbours, not its all-time median,
           so a lasting change in size is not mistaken for an error
  amber  possible, but looks like a copied or stale report
         - exactly the same non-zero inpatient count four weeks running
           (the field is a 7-day average, so exact repeats are implausible)
         - a one-week spike: occupancy jumps 30+ points and comes straight back

What the flags show is that a report is unreliable, not why. A frozen count
can be a stale data feed as easily as a choice. Say "unreliable", never "lie".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

#: Same non-zero value this many weeks in a row counts as frozen.
FROZEN_RUN = 4
#: Occupancy jump (fraction of beds) that counts as a spike.
SPIKE = 0.30
#: Neighbouring weeks must agree within this for a jump to be a spike.
SPIKE_NEIGHBOURS = 0.10
#: A bed count below this share of the neighbouring weeks' median is partial.
BED_COLLAPSE = 0.5
#: Weeks either side used as the neighbourhood for the bed-count check.
BED_WINDOW = 8
#: Averaging slack: 7-day averages can exceed a bed count by a fraction.
TOLERANCE = 0.5


@dataclass(frozen=True)
class Flag:
    hospital_id: str
    week_start: str
    rule: str
    severity: str          # "red" or "amber"
    detail: str

    def as_row(self) -> dict:
        return {"hospital_id": self.hospital_id, "week_start": self.week_start,
                "rule": self.rule, "severity": self.severity, "detail": self.detail}


def _num(v) -> float | None:
    return None if v is None else float(v)


def check_series(hospital_id: str, weeks: list[str], beds: list, used: list,
                 icu: list, icu_used: list) -> list[Flag]:
    """Flags for one hospital's weekly series (lists aligned with `weeks`)."""
    flags: list[Flag] = []
    n = len(weeks)
    beds = [_num(x) for x in beds]
    used = [_num(x) for x in used]
    icu = [_num(x) for x in icu]
    icu_used = [_num(x) for x in icu_used]
    ever_used = any(u and u > 0 for u in used)

    def local_median_beds(i: int) -> float | None:
        near = sorted(b for j, b in enumerate(beds[max(0, i - BED_WINDOW):i + BED_WINDOW + 1],
                                              start=max(0, i - BED_WINDOW))
                      if j != i and b)
        return near[len(near) // 2] if len(near) >= 4 else None

    def add(i: int, rule: str, sev: str, detail: str) -> None:
        flags.append(Flag(hospital_id, weeks[i], rule, sev, detail))

    for i in range(n):
        b, u, ib, iu = beds[i], used[i], icu[i], icu_used[i]
        if b is not None and u is not None and u > b + TOLERANCE:
            add(i, "patients_exceed_beds", "red",
                f"{u:g} inpatients reported in {b:g} staffed beds")
        if ib is not None and iu is not None and iu > ib + TOLERANCE:
            add(i, "icu_patients_exceed_icu_beds", "red",
                f"{iu:g} ICU patients reported in {ib:g} staffed ICU beds")
        if b is not None and ib is not None and ib > b + TOLERANCE:
            add(i, "icu_beds_exceed_inpatient_beds", "red",
                f"{ib:g} ICU beds but only {b:g} inpatient beds (ICU is part of inpatient)")
        usual = local_median_beds(i) if b else None
        if b and usual and b < BED_COLLAPSE * usual:
            add(i, "bed_count_collapse", "red",
                f"{b:g} staffed beds reported; the weeks around it report about "
                f"{usual:g}")
        if u == 0 and b and b > 0 and ever_used:
            add(i, "zero_patients_in_staffed_hospital", "red",
                f"0 inpatients reported in {b:g} staffed beds; other weeks report patients")

    run = 1
    for i in range(1, n):
        if used[i] is not None and used[i] > 0 and used[i] == used[i - 1]:
            run += 1
            if run == FROZEN_RUN:
                add(i, "frozen_count", "amber",
                    f"inpatient count {used[i]:g} identical for {FROZEN_RUN} weeks "
                    f"(a 7-day average rarely repeats exactly)")
            elif run > FROZEN_RUN:
                add(i, "frozen_count", "amber",
                    f"inpatient count {used[i]:g} identical for {run} weeks")
        else:
            run = 1

    for i in range(1, n - 1):
        b = beds[i]
        if not b or any(v is None for v in (used[i - 1], used[i], used[i + 1])):
            continue
        prev, cur, nxt = used[i - 1] / b, used[i] / b, used[i + 1] / b
        if (abs(cur - prev) >= SPIKE and abs(cur - nxt) >= SPIKE
                and abs(prev - nxt) <= SPIKE_NEIGHBOURS):
            add(i, "one_week_spike", "amber",
                f"occupancy {cur:.0%} between {prev:.0%} and {nxt:.0%}")
    return flags


def summarise(flags: Iterable[Flag], reports: int) -> dict:
    flags = list(flags)
    red = [f for f in flags if f.severity == "red"]
    by_rule: dict[str, int] = {}
    for f in flags:
        by_rule[f.rule] = by_rule.get(f.rule, 0) + 1
    return {
        "reports_checked": reports,
        "flags": len(flags),
        "red": len(red),
        "amber": len(flags) - len(red),
        "hospitals_with_red": len({f.hospital_id for f in red}),
        "by_rule": by_rule,
    }
