"""Run the live demo server.

    python -m mahros.server
    python -m mahros.server --port 9000 --reload
"""

from __future__ import annotations

import argparse


def main() -> None:
    p = argparse.ArgumentParser(description="MAHROS live demo server")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
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
