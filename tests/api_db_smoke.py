"""Exercise every database API of a running MAHROS, as the Hospital data tab does.

    docker compose up -d --build
    python tests/api_db_smoke.py                    # http://localhost:8090
    python tests/api_db_smoke.py http://127.0.0.1:8095

Changes data, then restores the real defaults at the end. Prints one line per
check and exits non-zero if any fails. Not part of pytest: it needs a live
server and a database.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8090").rstrip("/")
ACTOR = "smoke-test"
failures = 0


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps({**body, "actor": ACTOR}).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={
        "Content-Type": "application/json", "X-MAHROS-Actor": ACTOR})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def check(label: str, cond: bool, detail: str = "") -> None:
    global failures
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  ({detail})" if detail else ""))
    failures += 0 if cond else 1


def pool(hospitals: list, hid: str, kind: str) -> dict:
    h = next(x for x in hospitals if x["hospital_id"] == hid)
    return next(p for p in h["pools"] if p["kind"] == kind)


def main() -> int:
    code, st = call("GET", "/api/db/status")
    check("database connected", code == 200 and st.get("connected"), str(st)[:120])
    if not st.get("connected"):
        return 1

    code, r = call("POST", "/api/restore", {})
    check("restore everything to real data", code == 200 and r.get("ok"), str(r)[:120])

    code, r = call("GET", "/api/hospitals")
    hs = r["hospitals"]
    check("27 hospitals listed", len(hs) == 27)
    check("nothing edited after restore", not any(h["edited"] for h in hs))
    icu = pool(hs, "450358", "icu_bed")
    check("Houston Methodist ICU defaults to its reported week",
          icu["occupied"] == icu["default_occupied"], f"{icu['occupied']}/{icu['capacity']}")

    code, r = call("PATCH", "/api/hospitals/450358", {"occupied": {"icu_bed": 40},
                                                      "policy": "strategic"})
    check("save occupancy + behaviour", code == 200 and r.get("ok"), str(r)[:120])
    check("saved value read back", pool(r["hospitals"], "450358", "icu_bed")["occupied"] == 40)

    code, r = call("PATCH", "/api/hospitals/450358", {"occupied": {"icu_bed": 99999}})
    check("over-capacity refused with a reason", code == 400 and "capacity" in r.get("error", ""),
          r.get("error", ""))

    code, r = call("PATCH", "/api/hospitals/450358", {"policy": "liar"})
    check("unknown behaviour refused", code == 400)

    code, r = call("GET", "/api/hospitals/450358/history")
    hist = r["history"]
    check("history shows the change with who made it",
          any(x["actor"] == f"user:{ACTOR}" and (x.get("after") or {}).get("occupied") == 40
              for x in hist))

    code, r = call("GET", "/api/state")
    eng = next(h for h in r["hospitals"] if h["id"] == "450358")
    check("engine uses the database value",
          next(p for p in eng["pools"] if p["resource"] == "icu_bed")["occupied"] == 40)

    code, r = call("POST", "/api/transfer", {"origin": "450097", "resource": "icu_bed",
                                             "specialty": "cardiac", "acuity": 4})
    check("transfer runs", code == 200 and "request" in r, str(r)[:120])
    rid = r["request"]["request_id"]
    check("transfer saved", bool(r.get("db")), str(r.get("db")))

    code, r = call("GET", f"/api/transfers/{rid}")
    check("trace has every agent decision", code == 200 and len(r["decisions"]) >= 3,
          f"{len(r.get('decisions', []))} decisions")
    check("trace has audit rows", len(r.get("audit", [])) >= len(r.get("decisions", [])))

    code, r = call("GET", "/api/transfers?limit=5")
    check("transfer listed", any(t["request_id"] == rid for t in r["transfers"]))

    code, r = call("GET", "/api/audit/verify")
    check("audit chain intact", r.get("valid") is True, str(r)[:120])
    code, r = call("GET", "/api/ledger/verify")
    check("ledger intact", r.get("valid") is True, str(r)[:120])
    code, r = call("POST", "/api/audit/checkpoint", {})
    check("checkpoint written", code == 200 and r.get("checkpoint_id"), str(r)[:120])

    code, r = call("GET", "/api/data-integrity")
    check("data integrity summary", code == 200 and r["reports_checked"] > 5000,
          f"{r.get('red')} red / {r.get('amber')} amber")
    code, r = call("GET", "/api/hospitals/450358/flags")
    check("flags for one hospital", code == 200 and isinstance(r.get("flags"), list))

    code, r = call("POST", "/api/hospitals/450358/restore", {})
    check("restore one hospital", code == 200 and r.get("ok"), str(r)[:120])
    code, r = call("GET", "/api/hospitals")
    h = next(x for x in r["hospitals"] if x["hospital_id"] == "450358")
    check("that hospital is back to real data", not h["edited"] and h["policy"] == "honest")

    code, r = call("POST", "/api/restore", {})
    check("final restore", code == 200 and r.get("ok"))
    print("\nALL CHECKS PASS" if not failures else f"\n{failures} FAILURE(S)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
