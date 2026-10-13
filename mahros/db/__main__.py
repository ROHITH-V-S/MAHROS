"""Database commands.

    python -m mahros.db init          create schema, app role, load real data (safe to repeat)
    python -m mahros.db restore       live state back to the real defaults (new session)
    python -m mahros.db verify        recompute the audit chain and the transfer ledger
    python -m mahros.db flags         re-run the Data Integrity Agent over the real reports

`init` and `flags` run as the owner (MAHROS_ADMIN_URL); `restore` and `verify`
run as the application (MAHROS_DATABASE_URL), exactly as the dashboard would.
"""

from __future__ import annotations

import json
import sys


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "help"
    if cmd == "init":
        from .admin import init_database
        out = init_database()
        print(json.dumps(out, indent=2))
        return 0
    if cmd == "flags":
        from .admin import admin_connect
        from .integrity import load_flags
        with admin_connect() as conn:
            n = load_flags(conn)
            conn.commit()
        print(f"{n} flags stored")
        return 0
    if cmd in ("restore", "verify"):
        from .store import Store, actor_label
        store = Store.from_env()
        if store is None:
            print("MAHROS_DATABASE_URL is not set.", file=sys.stderr)
            return 2
        if cmd == "restore":
            print(json.dumps(store.restore(actor_label("command-line")), indent=2,
                             default=str))
            return 0
        epoch = store.current_epoch()["epoch"]
        audit = store.audit_verify()
        ledger = store.verify_ledger(epoch)
        print(json.dumps({"audit": audit, "ledger": ledger}, indent=2, default=str))
        return 0 if audit["valid"] and ledger["valid"] else 1
    print(__doc__)
    return 0 if cmd in ("help", "-h", "--help") else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
