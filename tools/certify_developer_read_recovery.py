"""Opt-in real-provider, read-only WebSocket certification of repaired source.

Uses private disposable state and a fresh local test token, never production state
or sockets. GitHub inspection is limited to the existing source branch's PR. All
write tools are denied independently of model instructions. No Codex turn runs.
"""

import argparse
import json
import logging
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading

from fastapi.testclient import TestClient

from developer_gateway.api_backend import OpenAIResponsesDeveloperBackend
from developer_gateway.backends import DeveloperBackends
from developer_gateway.state import READ_ONLY_TOOLS
from tools.certify_developer_recovery import BoundedClient


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, timeout=15)


def probe() -> None:
    status = json.loads(
        subprocess.check_output(["/usr/local/bin/jarvis-autonomy", "status"], timeout=5)
    )
    assert status["state"] == "STOPPED" and status["trusted"] is True
    root = Path(__file__).resolve().parents[1]
    before = git(root, "diff", "--no-ext-diff", "HEAD")
    branch = git(root, "branch", "--show-current").strip()
    directory = Path(tempfile.mkdtemp(prefix="jarvis-developer-read-recovery-"))
    os.environ["JARVIS_DEVELOPER_STATE_DIR"] = str(directory / "agent")
    os.environ["JARVIS_DEVELOPER_ATTACHMENT_DIR"] = str(directory / "attachments")
    os.environ["JARVIS_AUTHORITATIVE_WORKSPACE"] = str(root)
    os.environ["JARVIS_DEVELOPER_BACKEND"] = "api"
    os.environ["JARVIS_DEVELOPER_TOKEN"] = secrets.token_hex(32)
    # Alpha40 adds an optional observer: never inherit a live opt-in or journal.
    os.environ["JARVIS_AUTONOMY_OBSERVER_ENABLED"] = "false"
    os.environ["JARVIS_AUTONOMY_OBSERVER_STATE"] = str(directory / "observer.sqlite3")
    # Import after isolated configuration; never open the production task journal.
    from developer_gateway import app as gateway

    logging.getLogger("httpx").setLevel(logging.WARNING)
    done = threading.Event()
    events: list[str] = []
    clients: list[BoundedClient] = []

    def router() -> DeveloperBackends:
        result = DeveloperBackends(root)

        def backend_on_server_loop() -> OpenAIResponsesDeveloperBackend:
            if result.api is None:
                backend = OpenAIResponsesDeveloperBackend(root, directory / "agent")
                result.api = backend
                bounded = BoundedClient(backend.client)
                clients.append(bounded)
                backend.client = bounded
                backend.emergency_seconds = 120
                backend.retry_delays = (1.0,)  # type: ignore[assignment]
                execute = backend.runner.execute

                async def read_only(thread, name, args):
                    if name not in READ_ONLY_TOOLS:
                        raise RuntimeError("Read-only probe denies this tool")
                    return await execute(thread, name, args)

                async def observe(event):
                    events.append(event["method"])
                    if event.get("id") is not None:
                        row = backend.state.call(-event["id"])
                        await backend.respond(
                            -event["id"], {"decision": "decline"}, {row["thread"]}
                        )
                    if event["method"] == "turn/completed":
                        done.set()

                backend.runner.execute = read_only  # type: ignore[method-assign]
                backend.sinks.add(observe)
            return result.api

        result.api_backend = backend_on_server_loop  # type: ignore[method-assign]
        return result

    def auth(socket):
        socket.send_json({"type": "auth", "token": gateway.TOKEN})
        assert socket.receive_json()["type"] == "auth.ok"

    def request(socket, kind, identifier, **kwargs):
        socket.send_json({"type": kind, "request_id": identifier, "workspace": "jarvis", **kwargs})
        while True:
            event = socket.receive_json()
            assert event["type"] != "error"
            if event.get("request_id") == identifier:
                return event["result"]

    gateway.codex = router()
    with TestClient(gateway.app) as client:
        assert client.portal is not None
        assert gateway.observer is None
        assert client.get("/api/autonomy").status_code == 401
        assert (
            client.get(
                "/api/autonomy", headers={"Authorization": "Bearer " + gateway.TOKEN}
            ).status_code
            == 503
        )
        with client.websocket_connect("/api/developer") as socket:
            auth(socket)
            tid = request(socket, "thread.start", 1)["thread"]["id"]
            request(
                socket,
                "turn.start",
                2,
                thread_id=tid,
                text=(
                    "Read-only inspection. Call pr_status once even if this task has no branch. "
                    "Then use repository status, search for provider_failure_code, and read_file "
                    "developer_gateway/api_backend.py starting at line 1. Explain the PR result "
                    "and provider failure handling briefly. Do not create a worktree or change anything."
                ),
            )
            # Disconnect during actual streamed tool progress, not after completion.
            while True:
                message = socket.receive_json()
                if message.get("event", {}).get("method") == "item/started":
                    break
        assert done.wait(130), "Disconnected server job did not terminate"
        with client.websocket_connect("/api/developer") as socket:
            auth(socket)
            history = request(socket, "thread.resume", 3, thread_id=tid)["thread"]
            assert history["status"] == "completed"

            async def inspect_and_set_branch():
                backend = gateway.codex.api_backend()
                rows = [dict(r) for r in backend.state.db.execute("select * from calls")]
                assert {"pr_status", "repository", "search", "read_file"} <= {
                    r["tool"] for r in rows
                }
                assert all(r["state"] == "completed" for r in rows)
                pr = next(r for r in rows if r["tool"] == "pr_status")
                assert (
                    json.loads(pr["result"])["error"]
                    == "This task has no branch or pull request yet"
                )
                thread = backend.state.get(tid)
                # Read-only inspection of the existing source branch, never a new branch.
                thread["branch"] = branch
                backend.state.save(thread)

            client.portal.call(inspect_and_set_branch)
            done.clear()
            request(
                socket,
                "turn.start",
                4,
                thread_id=tid,
                text="Inspect pr_status once again for the now recorded existing branch. Report the actual GitHub result; do not retry if no PR exists. Read only.",
            )
            assert done.wait(130)
            history = request(socket, "thread.resume", 5, thread_id=tid)["thread"]
            assert history["status"] == "completed"

        async def ledger():
            backend = gateway.codex.api_backend()
            return [dict(r) for r in backend.state.db.execute("select * from calls order by id")]

        recorded = client.portal.call(ledger)
        assert all(r["state"] == "completed" and r["tool"] in READ_ONLY_TOOLS for r in recorded)
        pr_rows = [r for r in recorded if r["tool"] == "pr_status"]
        assert len(pr_rows) == 2
        github = json.loads(pr_rows[-1]["result"])
        assert "exit_code" in github
        assert github["ok"] or "no pull requests found" in github.get("output", "").lower()

    # Full repaired application lifecycle stop/start, without touching live services.
    gateway.codex = router()
    with TestClient(gateway.app) as client:
        assert client.portal is not None
        assert gateway.observer is None
        with client.websocket_connect("/api/developer") as socket:
            auth(socket)
            assert request(socket, "thread.resume", 6, thread_id=tid)["thread"] == history
            assert client.portal.call(ledger) == recorded
            done.clear()
            request(
                socket,
                "turn.start",
                7,
                thread_id=tid,
                text="Without more tools, briefly summarize the already observed PR inspection and provider handling. Do not claim a PR exists if none was found.",
            )
            assert done.wait(130)
            final = request(socket, "thread.resume", 8, thread_id=tid)["thread"]
            assert final["status"] == "completed" and len(final["turns"]) == 3
            assert client.portal.call(ledger) == recorded

    assert git(root, "diff", "--no-ext-diff", "HEAD") == before
    assert not (directory / "agent" / "worktrees").exists()
    assert not (directory / "observer.sqlite3").exists()
    assert {"item/started", "item/completed", "turn/completed", "item/agentMessage/delta"} <= set(
        events
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "directory": str(directory),
                "model": os.getenv("JARVIS_DEVELOPER_MODEL", "gpt-5.4"),
                "usage": final["usage"],
                "usage_incomplete": final["usage_incomplete"],
                "provider_requests": sum(c.requests for c in clients),
                "tool_calls": len(recorded),
                "pr_status_calls": 2,
                "real_websocket_disconnect_reconnect": True,
                "application_restart_history_and_ledger": True,
                "post_restart_provider_continuation": True,
                "source_unchanged": True,
                "write_tools_executed": 0,
                "alpha40_observer_disabled": True,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.parse_args()
    try:
        probe()
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "category": type(exc).__name__}))
        raise SystemExit(1) from None
