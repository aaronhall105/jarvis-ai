"""Observe-only sidecar using the same Governor/API as the optional gateway UI.

No Developer backend is instantiated. No model, worktree, broker worker or Git
operation is invoked. Default review interval is six hours; explicit observations
are also available through the authenticated API. This is not an autonomy daemon.
"""

import argparse
import asyncio
import json
import logging
import secrets
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI

from .autonomy import Governor
from .autonomy_api import router
from .autonomy_host import collect_report, guardian_status


async def review_loop(governor: Governor) -> None:
    while True:
        try:
            await governor.refresh(collect_report)
        except Exception:
            # Observation failure must not tear down manual Developer Mode.
            # Stale evidence expires; do not retry continuously or log exception contents.
            logging.getLogger("jarvis-observer").error("Observation cycle unavailable")
        await asyncio.sleep(6 * 60 * 60)


def create_app(path: Path, token: str) -> FastAPI:
    if not token:
        raise ValueError("Observer authentication required")
    active: list[Governor] = []

    @asynccontextmanager
    async def lifecycle(app):
        governor = Governor(path)
        active.append(governor)
        task = asyncio.create_task(review_loop(governor))
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            active.pop().close()

    app = FastAPI(title="Jarvis observe-only planning", lifespan=lifecycle)
    app.include_router(
        router(
            lambda: active[0] if active else None,
            lambda value: bool(value and secrets.compare_digest(value, "Bearer " + token)),
        )
    )
    return app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One read-only health observation; no engineering authority"
    )
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    governor = Governor(args.state)
    try:
        readings, unavailable = collect_report()
        governor.observe(readings, unavailable)
        snapshot = governor.snapshot(guardian_status())
        print(
            json.dumps(
                {
                    "phase": snapshot["phase"],
                    "status": snapshot["status"],
                    "observation_count": len(readings),
                    "unavailable_sources": list(unavailable),
                    "goal_count": len(snapshot["plans"]),
                    "idle": snapshot["current"] is None,
                    "development_execution_available": False,
                }
            )
        )
    finally:
        governor.close()


if __name__ == "__main__":
    main()
