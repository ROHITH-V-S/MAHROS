"""Run the Data Integrity Agent over the real reports stored in the database."""

from __future__ import annotations

import psycopg

from ..integrity.rules import check_series


def load_flags(conn: psycopg.Connection) -> int:
    """(Re)compute flags for every hospital. Idempotent; returns flags stored."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT hospital_id, week_start::text AS week, inpatient_beds, inpatient_used,
                   icu_beds, icu_used
            FROM hhs_weekly_reports ORDER BY hospital_id, week_start""")
        series: dict[str, dict[str, list]] = {}
        for r in cur.fetchall():
            s = series.setdefault(r["hospital_id"], {k: [] for k in
                                                     ("weeks", "beds", "used", "icu", "icu_used")})
            s["weeks"].append(r["week"])
            s["beds"].append(r["inpatient_beds"])
            s["used"].append(r["inpatient_used"])
            s["icu"].append(r["icu_beds"])
            s["icu_used"].append(r["icu_used"])
        flags = [f for hid, s in series.items() for f in check_series(hid, **s)]
        cur.execute("DELETE FROM report_flags")
        cur.executemany(
            "INSERT INTO report_flags (hospital_id, week_start, rule, severity, detail) "
            "VALUES (%s, %s, %s, %s, %s)",
            [(f.hospital_id, f.week_start, f.rule, f.severity, f.detail) for f in flags])
    return len(flags)
