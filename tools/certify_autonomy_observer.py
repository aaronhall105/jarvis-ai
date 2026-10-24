"""Bounded localhost Phase 1 probe. Never installs, activates or executes development.

Run from repository root with PYTHONPATH=. and the Developer Python environment.
Uses a fresh private journal and random in-memory token, not production credentials.
"""

from __future__ import annotations

import json
import multiprocessing
import secrets
import signal
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from developer_gateway.autonomy_host import guardian_status
from developer_gateway.observer import create_app

CANONICAL = "/home/aaron/jarvis-unified-production-worktree"


def _serve(path: str, token: str, listener: socket.socket) -> None:
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(create_app(Path(path), token), log_level="critical", access_log=False)
    )
    server.run(sockets=[listener])


def canonical_state() -> bytes:
    return subprocess.check_output(["git", "-C", CANONICAL, "status", "--porcelain=v2"])


def main() -> None:
    guardian = guardian_status()
    if guardian.get("state") != "STOPPED" or guardian.get("trusted") is not True:
        raise RuntimeError("Trusted STOPPED Guardian required")
    before = canonical_state()
    if before:
        raise RuntimeError("Canonical worktree is not clean")
    root = Path(tempfile.mkdtemp(prefix="jarvis-observer-http-phase1-"))
    journal = root / "roadmap.sqlite3"
    token = secrets.token_urlsafe(32)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    summaries = []
    prior_ids = None
    for cycle in range(2):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(16)
            port = listener.getsockname()[1]
            process = multiprocessing.get_context("fork").Process(
                target=_serve, args=(str(journal), token, listener)
            )
            process.start()
            try:
                url = f"http://127.0.0.1:{port}/api/autonomy"
                deadline = time.monotonic() + 20
                while True:
                    try:
                        with opener.open(url, timeout=1):
                            raise AssertionError("Missing authentication was accepted")
                    except urllib.error.HTTPError as error:
                        if error.code != 401:
                            raise
                        break
                    except (OSError, TimeoutError):
                        if time.monotonic() >= deadline:
                            raise RuntimeError("Sidecar startup unavailable") from None
                        time.sleep(0.1)
                headers = {"Authorization": "Bearer " + token}
                request = urllib.request.Request(
                    url + "/observe", data=b"", headers=headers, method="POST"
                )
                with opener.open(request, timeout=30) as response:
                    body = json.load(response)
                assert body["status"] == "STOPPED"
                assert body["technical"]["guardian_trusted"]
                assert not body["technical"]["development_execution_available"]
                ids = [item["id"] for item in body["plans"]]
                if prior_ids is not None:
                    assert ids == prior_ids
                prior_ids = ids
                # New HTTP connection, same server-owned journal.
                with opener.open(
                    urllib.request.Request(url, headers=headers), timeout=10
                ) as response:
                    reconnected = json.load(response)
                assert [item["id"] for item in reconnected["plans"]] == ids
                summaries.append(
                    {
                        "cycle": cycle,
                        "status": body["status"],
                        "goal_count": len(ids),
                        "idle": body["current"] is None,
                        "observation_count": body["technical"]["observations"],
                        "unavailable_sources": body["technical"]["unavailable_sources"],
                    }
                )
            finally:
                process.terminate()  # SIGTERM: uvicorn performs normal lifespan cleanup.
                process.join(20)
                if process.is_alive():
                    process.kill()
                    process.join()
                    raise RuntimeError("Sidecar failed graceful shutdown")
            # Uvicorn re-raises a captured SIGTERM after its lifespan shutdown.
            assert process.exitcode in (0, -signal.SIGTERM)
    with sqlite3.connect(journal) as db:
        assert db.execute("SELECT count(*) FROM calls").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM threads").fetchone()[0] == 0
    assert canonical_state() == before
    assert guardian_status()["state"] == "STOPPED"
    print(
        json.dumps(
            {
                "journal_directory": str(root),
                "cycles": summaries,
                "http_auth": "passed",
                "reconnect": "passed",
                "process_restart": "passed",
                "developer_jobs": 0,
                "tool_calls": 0,
                "canonical_unchanged": True,
            }
        )
    )


if __name__ == "__main__":
    main()
