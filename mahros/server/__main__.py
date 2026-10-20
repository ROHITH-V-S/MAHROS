"""Run the live demo server.

    python -m mahros.server
    python -m mahros.server --port 9000 --reload

HOST and PORT are read from the environment when no flag is given (Docker sets
them to listen on all interfaces). Otherwise the server stays on
127.0.0.1:8000: a demo server should not bind to every interface on someone's
laptop unless asked.
"""

from __future__ import annotations

import argparse
import os


def main() -> None:
    p = argparse.ArgumentParser(description="MAHROS live demo server")
    p.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    p.add_argument("--reload", action="store_true")
    args = p.parse_args()

    try:
        import uvicorn
    except ImportError:
        raise SystemExit("uvicorn is required:  pip install 'uvicorn[standard]' fastapi")

    print(f"\n  MAHROS live demo  ->  http://{args.host}:{args.port}\n")
    uvicorn.run(
        "mahros.server.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
