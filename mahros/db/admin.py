"""Create and fill the database. Runs as the database owner, never as the app.

    python -m mahros.db init        # schema + app role + real data (idempotent)
    python -m mahros.db restore     # put the live state back to the real defaults
    python -m mahros.db verify      # check the audit chain and the ledger

Needs MAHROS_ADMIN_URL (the owner) and, for `init`, MAHROS_APP_PASSWORD (the
password given to the restricted `mahros_app` role the dashboard uses).
"""

from __future__ import annotations

import os
from importlib import resources as pkg
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .defaults import build_reference
from .store import plain_url

ADMIN_ENV = "MAHROS_ADMIN_URL"
APP_PASSWORD_ENV = "MAHROS_APP_PASSWORD"
DEFAULT_METRO = "houston"
DEFAULT_WEEK = "2021-01-10"     # the reference week of the Houston network


def _schema_sql() -> str:
    return pkg.files("mahros.db").joinpath("schema.sql").read_text(encoding="utf-8")


def admin_connect(url: str | None = None) -> psycopg.Connection:
    url = url or os.environ.get(ADMIN_ENV, "")
    if not url:
        raise SystemExit(f"{ADMIN_ENV} is not set (the database owner's connection URL).")
    return psycopg.connect(plain_url(url), row_factory=dict_row, autocommit=False)


def ensure_app_role(conn: psycopg.Connection, password: str) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'mahros_app'")
        if cur.fetchone() is None:
            cur.execute(sql.SQL("CREATE ROLE mahros_app LOGIN PASSWORD {}")
                        .format(sql.Literal(password)))
        else:
            cur.execute(sql.SQL("ALTER ROLE mahros_app LOGIN PASSWORD {}")
                        .format(sql.Literal(password)))
        cur.execute("SELECT current_database() AS db")
        db = cur.fetchone()["db"]
        cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO mahros_app")
                    .format(sql.Identifier(db)))
        cur.execute("GRANT USAGE ON SCHEMA public TO mahros_app")


def load_reference(conn: psycopg.Connection, metro: str = DEFAULT_METRO,
                   week: str = DEFAULT_WEEK) -> dict[str, int]:
    """Load the real data once; on later runs, refresh the derived defaults only."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM hospitals")
        if cur.fetchone()["n"]:
            return refresh_defaults(conn, metro, week)
        ref = build_reference(metro, week)
        cur.execute("SELECT set_config('mahros.loading_reference', 'on', true)")
        cur.execute("SELECT set_config('mahros.actor', 'system:data-loader', true)")

        def put(table: str, rows: list[dict[str, Any]]) -> None:
            if not rows:
                return
            cols = list(rows[0])
            q = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(table),
                sql.SQL(", ").join(map(sql.Identifier, cols)),
                sql.SQL(", ").join(sql.Placeholder() * len(cols)))
            cur.executemany(q, [tuple(r[c] for c in cols) for r in rows])

        put("hospitals", ref.hospitals)
        put("hospital_capabilities", ref.capabilities)
        put("hhs_weekly_reports", ref.weekly_reports)
        put("travel_times", ref.travel_times)
        put("resources", [{**r, "occupied": r["default_occupied"]} for r in ref.resources])
        put("hospital_settings", [{"hospital_id": h["hospital_id"], "policy": "honest"}
                                  for h in ref.hospitals])
        cur.execute("""
            INSERT INTO live_epochs (started_by, reason, metro, reference_week)
            VALUES ('system:data-loader', 'first load', %s, %s)""", (metro, ref.week))
        cur.execute("INSERT INTO system_events (level, source, event_type, details) "
                    "VALUES ('info', 'loader', 'reference_loaded', %s)",
                    (Jsonb({"metro": metro, "week": ref.week, "source": ref.source,
                            "hospitals": len(ref.hospitals),
                            "weekly_reports": len(ref.weekly_reports)}),))
        return {"hospitals": len(ref.hospitals), "capabilities": len(ref.capabilities),
                "weekly_reports": len(ref.weekly_reports),
                "travel_times": len(ref.travel_times), "resources": len(ref.resources)}


def refresh_defaults(conn: psycopg.Connection, metro: str = DEFAULT_METRO,
                     week: str = DEFAULT_WEEK) -> dict[str, int]:
    """Re-derive each pool's capacity and default occupancy from the source.

    The HHS rows never change, but the rule that turns them into pool defaults
    can (e.g. procedure rooms now start empty). Only this loader may write
    those locked columns; each change is audited as system:data-loader. Live
    occupancy is clamped to a lowered capacity and otherwise left alone.
    """
    ref = build_reference(metro, week)
    changed = 0
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('mahros.loading_reference', 'on', true)")
        cur.execute("SELECT set_config('mahros.actor', 'system:data-loader', true)")
        for r in ref.resources:
            cur.execute("""
                UPDATE resources SET capacity = %(capacity)s,
                    capacity_provenance = %(capacity_provenance)s,
                    capacity_source = %(capacity_source)s,
                    default_occupied = %(default_occupied)s,
                    default_provenance = %(default_provenance)s,
                    default_source = %(default_source)s,
                    occupied = LEAST(occupied, %(capacity)s)
                WHERE hospital_id = %(hospital_id)s AND kind = %(kind)s
                  AND (capacity, capacity_provenance, capacity_source, default_occupied,
                       default_provenance, default_source)
                      IS DISTINCT FROM (%(capacity)s, %(capacity_provenance)s,
                                        %(capacity_source)s, %(default_occupied)s,
                                        %(default_provenance)s, %(default_source)s)""", r)
            changed += cur.rowcount
        if changed:
            cur.execute("INSERT INTO system_events (level, source, event_type, details) "
                        "VALUES ('info', 'loader', 'defaults_refreshed', %s)",
                        (Jsonb({"pools_changed": changed}),))
    return {"defaults_refreshed": changed} if changed else {}


def init_database(admin_url: str | None = None, app_password: str | None = None,
                  metro: str = DEFAULT_METRO, week: str = DEFAULT_WEEK) -> dict[str, Any]:
    password = app_password or os.environ.get(APP_PASSWORD_ENV, "")
    if not password:
        raise SystemExit(f"{APP_PASSWORD_ENV} is not set (password for the mahros_app role).")
    with admin_connect(admin_url) as conn:
        ensure_app_role(conn, password)
        conn.execute(_schema_sql())
        loaded = load_reference(conn, metro, week)
        from .integrity import load_flags
        flags = load_flags(conn)
        conn.commit()
    return {"loaded": loaded, "report_flags": flags}
