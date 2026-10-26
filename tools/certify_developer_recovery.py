"""Explicit, isolated live-API repair probe. Never targets the Jarvis repository.

Uses only the existing dedicated Developer credential mechanism. No key is copied
or printed. No protected operation is approved. Retains disposable probe evidence.
"""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from developer_gateway.api_backend import OpenAIResponsesDeveloperBackend


class BoundedClient:
    """Certification-only limits around the real SDK, never production quotas."""

    def __init__(self, sdk: Any):
        self.sdk = sdk
        self.responses = self
        self.requests = 0

    async def create(self, **kwargs: Any) -> Any:
        if self.requests >= 12:
            raise RuntimeError("Probe request limit reached")
        self.requests += 1
        return await self.sdk.responses.create(**kwargs, max_output_tokens=2048)

    async def close(self) -> None:
        await self.sdk.close()


async def probe(reconciliation: bool = False) -> None:
    guardian = subprocess.run(
        ["/usr/local/bin/jarvis-autonomy", "status"],
        capture_output=True,
        check=True,
        timeout=5,
        text=True,
    )
    status = json.loads(guardian.stdout)
    if status.get("state") != "STOPPED" or status.get("trusted") is not True:
        raise RuntimeError("Trusted STOPPED Guardian required")
    directory = Path(tempfile.mkdtemp(prefix="jarvis-developer-recovery-probe-"))
    root = directory / "source"
    root.mkdir(mode=0o700)
    source = "developer_probe.py" if reconciliation else "arithmetic.py"
    test = "test_developer_probe.py" if reconciliation else "test_arithmetic.py"
    if reconciliation:
        (root / source).write_text(
            "def pr_status(thread, inspect):\n"
            '    if not thread.get("branch"):\n'
            '        raise ValueError("No task branch")\n'
            '    return inspect(thread["branch"])\n'
        )
        (root / test).write_text(
            "from developer_probe import pr_status\n\n"
            "def test_no_branch_is_a_completed_read():\n"
            "    def forbidden(branch):\n"
            '        raise AssertionError("No external operation expected")\n'
            '    assert pr_status({}, forbidden) == {"ok": False, "error": "This task has no branch or pull request yet"}\n\n'
            "def test_existing_branch_is_inspected_once():\n"
            "    calls = []\n"
            "    def inspect(branch):\n"
            "        calls.append(branch)\n"
            '        return {"ok": True}\n'
            '    assert pr_status({"branch": "jarvis/task"}, inspect) == {"ok": True}\n'
            '    assert calls == ["jarvis/task"]\n'
        )
    else:
        (root / source).write_text("def answer():\n    return 1\n")
        (root / test).write_text(
            "from arithmetic import answer\n\ndef test_answer():\n    assert answer() == 2\n"
        )
    backend = OpenAIResponsesDeveloperBackend(root, directory / "agent")
    backend.emergency_seconds = 180
    try:
        if not backend.configured:
            raise RuntimeError("Dedicated Developer credential unavailable")
        bounded = BoundedClient(backend.client)
        backend.client = bounded
        for args in (
            ("init", "-b", "jarvis/unified-production"),
            ("add", "."),
            (
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@localhost",
                "commit",
                "-m",
                "Disposable fixture",
            ),
        ):
            result = await backend.runner.git(root, *args)
            if not result["ok"]:
                raise RuntimeError("Fixture preparation failed")
        before = hashlib.sha256((root / source).read_bytes()).hexdigest()
        disconnected = False

        async def sink(event):
            nonlocal disconnected
            if "id" in event:
                await backend.respond(-event["id"], {"decision": "decline"}, {thread["id"]})
            if event.get("method") == "item/started" and not disconnected:
                disconnected = True
                raise ConnectionError("Disposable client disconnected")

        thread = backend.state.create(str(root))
        backend.sinks.add(sink)
        await backend.request(
            "turn/start",
            {
                "threadId": thread["id"],
                "input": [
                    {
                        "type": "text",
                        "text": (
                            f"Repair this disposable fixture only. Inspect {source} and {test}. "
                            f"Create your isolated worktree. Change only {source} to satisfy the unchanged tests, "
                            "using one patch_file call. Do not change the test. Inspect the diff, run the "
                            f"pytest verification profile on {test} and report the observed result. "
                            "Do not commit, push, create a PR, deploy, or request protected operations."
                        ),
                    }
                ],
            },
        )
        await backend.jobs[thread["id"]]
        saved = backend.state.get(thread["id"])
        counts = dict(backend.state.db.execute("select tool,count(*) from calls group by tool"))
        if saved["status"] != "completed":
            print(
                json.dumps(
                    {
                        "status": saved["status"],
                        "error": saved.get("error"),
                        "directory": str(directory),
                    }
                )
            )
            raise RuntimeError("Live probe did not complete")
        rows = list(backend.state.db.execute("select tool,state,result from calls"))
        verified = [json.loads(row["result"]) for row in rows if row["tool"] == "verify"]
        assert counts.get("patch_file") == 1 and verified and all(v["ok"] for v in verified)
        assert all(row["state"] == "completed" for row in rows)
        assert not (set(counts) & {"git_commit", "git_push", "pr_create", "pr_update", "pr_merge"})
        assert hashlib.sha256((root / source).read_bytes()).hexdigest() == before
        patched = Path(saved["worktree"]) / source
        patched_hash = hashlib.sha256(patched.read_bytes()).hexdigest()
        patch = dict(
            backend.state.db.execute("select * from calls where tool='patch_file'").fetchone()
        )
        assert json.loads(patch["arguments"])["path"] == source
        assert (Path(saved["worktree"]) / test).read_bytes() == (root / test).read_bytes()
        for row in backend.state.db.execute("select arguments from calls where tool='verify'"):
            assert json.loads(row["arguments"]) == {"profile": "pytest", "path": test}
        history = backend.public_thread(saved)
        await backend.stop()
        restored = OpenAIResponsesDeveloperBackend(root, directory / "agent", key="")
        try:
            assert restored.public_thread(restored.state.get(thread["id"])) == history
            assert restored.state.db.execute("select count(*) from calls").fetchone()[0] == len(
                rows
            )

            async def no_execution(*args):
                raise AssertionError("Completed patch must not execute again")

            restored.runner.execute = no_execution  # type: ignore[method-assign]
            assert await restored.tool(
                saved,
                {
                    "name": "patch_file",
                    "call_id": patch["call_id"],
                    "arguments": patch["arguments"],
                },
            ) == json.loads(patch["result"])
            assert hashlib.sha256(patched.read_bytes()).hexdigest() == patched_hash
            assert restored.state.db.execute("select count(*) from calls").fetchone()[0] == len(
                rows
            )
        finally:
            await restored.stop()
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "backend": "api",
                    "disconnected_client_survived": disconnected,
                    "restart_history_and_ledger": True,
                    "patch_executions": counts["patch_file"],
                    "completed_patch_replay_cached": True,
                    "scenario": "pr_status_repair" if reconciliation else "arithmetic_repair",
                    "provider_requests": bounded.requests,
                    "usage": saved["usage"],
                    "usage_incomplete": saved.get("usage_incomplete", False),
                    "tools": counts,
                    "source_unchanged": True,
                    "directory": str(directory),
                },
                sort_keys=True,
            )
        )
    finally:
        await backend.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--reconciliation", action="store_true")
    options = parser.parse_args()
    # Existing restricted verification boundary, never Docker-group authority.
    os.environ["JARVIS_DEVELOPER_SANDBOX"] = "bwrap"
    try:
        asyncio.run(probe(options.reconciliation))
    except Exception:
        print("Disposable recovery probe failed; production and credentials untouched")
        raise SystemExit(1) from None
