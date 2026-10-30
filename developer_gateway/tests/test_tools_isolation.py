import asyncio
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

from developer_gateway.api_backend import OpenAIResponsesDeveloperBackend
from developer_gateway.tools import ToolRunner
from developer_gateway.tests.test_api_backend import Client, response, call


def test_docker_boundary(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DEVELOPER_SANDBOX", "docker")
    runner = ToolRunner(tmp_path, tmp_path / "worktrees", Path(sys.executable))
    argv = runner.sandbox(
        tmp_path / "worktrees/api_owned", [str(runner.python), "-m", "pytest"], True
    )
    assert argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv and "ALL" in argv and "no-new-privileges:true" in argv
    assert "--memory" in argv and "--pids-limit" in argv and "--user" in argv
    assert not any("docker.sock" in arg or "credential" in arg for arg in argv)
    assert "/usr/bin/timeout" in argv
    assert "type=bind,src=/dev/null,dst=/workspace/.git,readonly" in argv


@pytest.mark.asyncio
async def test_docker_startup_reconciles_only_owned_containers(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DEVELOPER_SANDBOX", "docker")
    runner = ToolRunner(tmp_path, tmp_path / "worktrees", Path(sys.executable))
    calls = []

    async def command(argv, cwd):
        calls.append(argv)
        return {"ok": True, "output": "abc123abc123\n" if "ps" in argv else ""}

    runner.command = command
    await runner.reconcile()
    await runner.reconcile()
    assert len(calls) == 2
    assert calls[0][-1] == "label=io.jarvis.developer.owner=" + runner.owner
    assert calls[1] == ["/usr/bin/docker", "rm", "-f", "abc123abc123"]


@pytest.fixture
async def repository(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    runner = ToolRunner(root, tmp_path / "worktrees", Path(sys.executable))
    await runner.git(root, "init", "-b", "jarvis/unified-production")
    (root / "example.py").write_text("value = 1\n")
    await runner.git(root, "add", ".")
    result = await runner.git(
        root, "-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-m", "fixture"
    )
    assert result["ok"]
    return runner


@pytest.mark.asyncio
async def test_real_isolated_worktree_patch_diff_and_replay(repository, tmp_path, backend_factory):
    backend = backend_factory(
        repository.root, tmp_path / "agent", client=Client(response()), key=""
    )
    thread = backend.state.create(str(repository.root))
    thread.update(turn_id="turn", status="running")
    backend.state.save(thread)
    await backend.tool(thread, call("create_worktree", {}, "create"))
    workspace = Path(thread["worktree"])
    assert workspace != repository.root
    original = (workspace / "example.py").read_bytes()
    patch = call(
        "patch_file",
        {
            "path": "example.py",
            "expected_sha256": hashlib.sha256(original).hexdigest(),
            "content": "value = 2\n",
        },
        "patch",
    )
    result = await backend.tool(thread, patch)
    assert (repository.root / "example.py").read_text() == "value = 1\n"
    assert (workspace / "example.py").read_text() == "value = 2\n"
    (workspace / "new.py").write_text("new_value = True\n")
    diff = await backend.runner.execute(thread, "repository", {"view": "diff"})
    assert "+new_value = True" in diff["output"]
    assert backend.runner.source(workspace, "developer_gateway", directory=True).is_relative_to(
        workspace
    )
    # Reopen the durable journal as a new process would; no model/tool callback needed.
    await backend.stop()
    restored = backend_factory(
        repository.root, tmp_path / "agent", client=Client(response()), key=""
    )

    async def must_not_run(*args):
        raise AssertionError("Completed side effect was replayed")

    restored.runner.execute = must_not_run
    assert await restored.tool(thread, patch) == result


@pytest.mark.asyncio
async def test_branch_switch_prevents_writes(repository):
    thread = {"id": "api_owned"}
    await repository.execute(thread, "create_worktree", {})
    await repository.git(Path(thread["worktree"]), "checkout", "--detach")
    with pytest.raises(ValueError, match="ownership"):
        await repository.execute(
            thread,
            "patch_file",
            {
                "path": "new.py",
                "expected_sha256": "",
                "content": "x",
            },
        )


@pytest.mark.skipif(not shutil.which("bwrap"), reason="Bubblewrap unavailable on this test host")
@pytest.mark.asyncio
async def test_real_sandbox_has_no_home_secrets_network_or_git_metadata(repository, tmp_path):
    thread = {"id": "api_isolated"}
    await repository.execute(thread, "create_worktree", {})
    root = Path(thread["worktree"])
    private = tmp_path / "private-credential"
    private.write_text("not-for-child")
    script = root / "tools/check_boundary.sh"
    script.parent.mkdir()
    script.write_text(
        f'test ! -e "{private}"\n'
        "test ! -e /var/run/docker.sock\n"
        "test ! -s /workspace/.git\n"
        'test -z "$OPENAI_API_KEY"\n'
        "test ! -e /etc/resolv.conf\n"
        "echo isolated\n"
    )
    result = await repository.execute(
        thread,
        "verify",
        {
            "profile": "script",
            "path": "tools/check_boundary.sh",
        },
    )
    assert result["ok"], result
    assert "isolated" in result["output"]


@pytest.mark.asyncio
async def test_approval_cannot_cross_threads_or_turns(tmp_path, backend_factory):
    backend = backend_factory(tmp_path, tmp_path / "agent", client=Client(response()), key="")
    thread = backend.state.create(str(tmp_path))
    thread.update(turn_id="one", status="running")
    backend.state.save(thread)
    row = backend.state.claim(thread["id"], "one", "call", "git_push", {})
    backend.state.update_call(row["id"], approval="pending", expires=10**12)
    with pytest.raises(ValueError):
        await backend.respond(row["id"], {"decision": "accept"}, {"api_another"})
    forged = {**thread, "turn_id": "another"}
    with pytest.raises(ValueError, match="ownership"):
        await backend.tool(forged, call())


@pytest.mark.asyncio
async def test_concurrent_threads_have_independent_jobs(tmp_path, backend_factory):
    backend = backend_factory(
        tmp_path,
        tmp_path / "agent",
        client=Client(response(text="one"), response(text="two")),
        key="",
    )
    threads = [backend.state.create(str(tmp_path)) for _ in range(2)]
    for thread in threads:
        await backend.request(
            "turn/start",
            {"threadId": thread["id"], "input": [{"type": "text", "text": thread["id"]}]},
        )
    await asyncio.gather(*backend.jobs.values())
    states = [backend.state.get(t["id"]) for t in threads]
    assert all(t["status"] == "completed" for t in states)
    assert states[0]["turn_id"] != states[1]["turn_id"]
    assert states[0]["turns"][0]["items"][0] != states[1]["turns"][0]["items"][0]
