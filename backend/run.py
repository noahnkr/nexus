"""Application entrypoint — start the backend with this, not with `uvicorn`.

    python run.py                 # http://127.0.0.1:8000
    python run.py --reload        # local development
    python run.py --host 0.0.0.0 --port 8080

WHY THIS FILE EXISTS: psycopg's async pool cannot run on Windows' default
ProactorEventLoop, and the policy has to be set BEFORE the loop is created.
Uvicorn creates it in `Server.run()` and only *then* imports the application
(`config.load()` runs inside `serve()`), so a `set_event_loop_policy` call at
the top of `app/main.py` executes after the loop already exists and changes
nothing. That is why `python -m uvicorn app.main:app` fails on Windows with

    PoolTimeout: pool initialization incomplete after 30.0 sec

— a message that reads like bad database credentials and is nothing of the
kind. `--reload` masks it, because uvicorn's own `asyncio_setup()` sets the
selector policy when it is going to spawn a subprocess; that is luck, not
design, and it disappears the moment someone drops the flag in a Dockerfile or
a service unit.

Setting the policy here, before `uvicorn` is even imported, is the only place
that reliably runs first. On non-Windows platforms this file is a thin wrapper
and the policy block is skipped.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn  # noqa: E402 — must follow the policy call above


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Nexus backend.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--reload",
        action="store_true",
        help="restart on code changes (development only)",
    )
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
