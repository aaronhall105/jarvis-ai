"""Real disposable SQLite/Git request preparation; no provider or production IO."""

import ast
import asyncio
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import subprocess
from types import SimpleNamespace
from typing import Any
import uuid
from unittest.mock import AsyncMock

from fastapi import FastAPI, Header, HTTPException
import httpx
from pydantic import BaseModel, Field
import pytest

from app.ai_engine import AIEngine, RequestIntent, RoutingDecision
from app.development_requests import DevelopmentRequestError, MAX_SOURCE_BLOB_BYTES
from app.development_scope import editable_path
from app.realtime_voice import RealtimeVoiceConfig, RealtimeVoiceProxy, sanitise_tool_events
from app.self_improvement import SelfImprovementEngine
from app.user_context import UserContext
from tools import self_improvement_worker as legacy_worker
from tools.supervised_development_runtime import GovernorCandidateSource


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def test_owner_request_preparation_code_is_not_an_autonomous_edit_target():
    assert not editable_path("bridge/app/development_requests.py")


@pytest.fixture
def case(tmp_path):
    root = tmp_path / "source"
    (root / "app").mkdir(parents=True)
    (root / "app/responder.py").write_text("def response():\n    return 'ready'\n")
    (root / "app/test_responder.py").write_text("def test_response():\n    assert True\n")
    (root / "tools").mkdir()
    (root / "tools/supervised_development_runtime.py").write_text("SECRET = 'protected'\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "base",
    )
    engine = SelfImprovementEngine(str(tmp_path / "fixture.db"), development_source_root=str(root))
    return engine, root


def scope(root):
    return {
        "source_sha": git(root, "rev-parse", "HEAD"),
        "source_findings": {
            path: {
                "sha256": hashlib.sha256((root / path).read_bytes()).hexdigest(),
                "anchor": anchor,
            }
            for path, anchor in {
                "app/responder.py": "def response():",
                "app/test_responder.py": "def test_response():",
            }.items()
        },
        "acceptance_criteria": [
            "Preserve the existing ready response and add a deterministic regression test."
        ],
    }


async def request(engine, request_id=None):
    return await engine.request_development(
        request_id or str(uuid.uuid4()), "Improve time to the first useful response safely", "aaron"
    )


def rows(engine, table):
    with sqlite3.connect(engine.database_path) as connection:
        return connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()


@pytest.mark.asyncio
async def test_request_is_atomic_idempotent_and_preserves_owner_enhancement(case):
    engine, _ = case
    identifier = str(uuid.uuid4())
    responses = await asyncio.gather(*(request(engine, identifier) for _ in range(8)))
    assert all(result == responses[0] for result in responses)
    assert responses[0]["status"] == "needs_review"
    for table in (
        "improvement_interactions",
        "improvement_failures",
        "improvement_candidates",
        "improvement_audit",
    ):
        assert len(rows(engine, table)) == 1
    failure = await engine.get_failure(responses[0]["failure_id"])
    assert failure["category"] == "owner_enhancement"
    assert failure["evidence"]["confirmed_defect"] is False
    assert failure["evidence"]["source"]["request_id"] == identifier
    assert "first useful response" in failure["evidence"]["source"]["request"]
    before = rows(engine, "improvement_failures")
    with pytest.raises(DevelopmentRequestError, match="request_id_conflict"):
        await engine.request_development(identifier, "A different owner request", "aaron")
    assert rows(engine, "improvement_failures") == before


@pytest.mark.asyncio
async def test_restart_returns_same_durable_request(case):
    engine, root = case
    first = await request(engine)
    restarted = SelfImprovementEngine(str(engine.database_path), development_source_root=str(root))
    assert await request(restarted, first["request_id"]) == first


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [False, True])
async def test_legacy_workers_never_claim_owner_requests(case, monkeypatch, prepared):
    engine, root = case
    result = await request(engine)
    if prepared:
        await engine.propose_development_scope(result["request_id"], "aaron", **scope(root))
    else:
        # An absent scope, including legacy empty-string representation, does
        # not give either legacy loop ownership of a registered request.
        with sqlite3.connect(engine.database_path) as connection:
            connection.execute("UPDATE improvement_candidates SET development_scope_json = ''")
    before = rows(engine, "improvement_candidates")
    monkeypatch.setattr(legacy_worker, "DB_PATH", engine.database_path)
    assert legacy_worker.fetch_candidate(("queued",)) is None
    assert rows(engine, "improvement_candidates") == before


@pytest.mark.asyncio
async def test_verified_scope_does_not_rewrite_original_history(case):
    engine, root = case
    await engine.request_improvement("Preserve this historical user request", "fixture")
    prior = rows(engine, "improvement_failures")
    response = await request(engine)
    original = rows(engine, "improvement_failures")
    findings = scope(root)
    result = await engine.propose_development_scope(response["request_id"], "aaron", **findings)
    assert result["status"] == "ready"
    assert result["affected_files"] == sorted(findings["source_findings"])
    assert result["source_sha"] == findings["source_sha"]
    assert result["scope_kind"] == "owner_enhancement_proposal"
    assert result["approval_required"] is True and result["task_id"] is None
    assert rows(engine, "improvement_failures") == original
    assert original[: len(prior)] == prior
    audit = rows(engine, "improvement_audit")
    assert (
        await engine.propose_development_scope(response["request_id"], "aaron", **findings)
        == result
    )
    assert rows(engine, "improvement_audit") == audit
    findings["acceptance_criteria"] = ["Change the approved meaning to something else"]
    with pytest.raises(DevelopmentRequestError, match="scope_already_bound"):
        await engine.propose_development_scope(response["request_id"], "aaron", **findings)


@pytest.mark.asyncio
async def test_actual_owner_request_reaches_real_governor_without_fabricated_failure(case):
    engine, root = case
    response = await request(engine)
    reader = GovernorCandidateSource(engine.database_path, root)
    try:
        assert reader.next() is None
        original = rows(engine, "improvement_failures")
        prepared = await engine.propose_development_scope(
            response["request_id"], "aaron", **scope(root)
        )
        candidate = reader.next()
        assert candidate["candidate_id"] == response["candidate_id"]
        assert candidate["evidence"]["kind"] == "owner_enhancement_proposal"
        assert candidate["evidence"]["source_sha"] == prepared["source_sha"]
        assert candidate["allowed_paths"] == tuple(prepared["affected_files"])
        assert candidate["evidence"]["confirmed_defect"] is False
        assert rows(engine, "improvement_failures") == original
    finally:
        reader.close()


@pytest.mark.asyncio
async def test_read_status_never_calls_tampered_source_proposal_ready(case):
    engine, root = case
    response = await request(engine)
    await engine.propose_development_scope(response["request_id"], "aaron", **scope(root))
    with sqlite3.connect(engine.database_path) as connection:
        stored = json.loads(
            connection.execute(
                "SELECT development_scope_json FROM improvement_candidates"
            ).fetchone()[0]
        )
        stored["source_findings"]["app/responder.py"]["anchor"] = "invented implementation"
        connection.execute(
            "UPDATE improvement_candidates SET development_scope_json = ?", (json.dumps(stored),)
        )
    before = rows(engine, "improvement_candidates")
    result = await engine.development_request(response["request_id"], "aaron")
    assert result["status"] == "blocked"
    assert result["reason"] == "source_finding_not_verified"
    assert rows(engine, "improvement_candidates") == before


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["stale", "hash", "anchor", "protected", "alias", "criteria"])
async def test_unverified_or_protected_scope_cannot_be_prepared(case, defect):
    engine, root = case
    response = await request(engine)
    findings = scope(root)
    if defect == "stale":
        findings["source_sha"] = "a" * 40
    elif defect == "hash":
        findings["source_findings"]["app/responder.py"]["sha256"] = "a" * 64
    elif defect == "anchor":
        findings["source_findings"]["app/responder.py"]["anchor"] = "an invented implementation"
    elif defect in {"protected", "alias"}:
        path = (
            "tools/supervised_development_runtime.py"
            if defect == "protected"
            else "app/../app/responder.py"
        )
        findings["source_findings"][path] = findings["source_findings"].pop("app/responder.py")
    else:
        findings["acceptance_criteria"] = []
    before = rows(engine, "improvement_candidates")
    with pytest.raises(DevelopmentRequestError):
        await engine.propose_development_scope(response["request_id"], "aaron", **findings)
    assert rows(engine, "improvement_candidates") == before


@pytest.mark.asyncio
async def test_inspection_reads_only_eligible_tracked_blobs_and_detects_drift(case):
    engine, root = case
    source = await engine.inspect_development_source("response")
    assert {item["path"] for item in source["files"]} == {
        "app/responder.py",
        "app/test_responder.py",
    }
    assert await engine.inspect_development_source("SECRET") == {
        "source_sha": source["source_sha"],
        "files": [],
        "read_only": True,
    }
    with pytest.raises(DevelopmentRequestError, match="source_path_not_editable"):
        await engine.inspect_development_source("SECRET", "tools/supervised_development_runtime.py")
    result = await request(engine)
    await engine.propose_development_scope(result["request_id"], "aaron", **scope(root))
    (root / "app/responder.py").write_text("def response():\n    return 'changed'\n")
    assert (await engine.development_request(result["request_id"], "aaron"))["status"] == "blocked"
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "change",
    )
    assert (await engine.development_request(result["request_id"], "aaron"))[
        "reason"
    ] == "source_binding_stale"


@pytest.mark.asyncio
async def test_source_inspection_ignores_inherited_git_overrides_and_fsmonitor(case, monkeypatch):
    engine, root = case
    expected = git(root, "rev-parse", "HEAD")
    # No caller environment or repository fsmonitor program may run as part of
    # the trusted read-only source inspection.
    git(root, "config", "core.fsmonitor", "/nonexistent-fixture-monitor")
    monkeypatch.setenv("GIT_DIR", "/nonexistent-fixture-git-dir")
    source = await engine.inspect_development_source("response")
    assert source["files"]
    assert source["source_sha"] == expected


@pytest.mark.asyncio
async def test_normal_large_application_file_can_be_inspected_and_scope_verified(case):
    engine, root = case
    source_file = root / "app/responder.py"
    source_file.write_text(
        "def response():\n    return 'ready'\n" + "# application source\n" * 26_000
    )
    assert 500_000 < source_file.stat().st_size <= MAX_SOURCE_BLOB_BYTES
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "large source",
    )
    inspected = await engine.inspect_development_source("response", "app/responder.py")
    assert inspected["files"][0]["sha256"] == hashlib.sha256(source_file.read_bytes()).hexdigest()
    assert len(inspected["files"][0]["excerpt"]) <= 2000
    response = await request(engine)
    prepared = await engine.propose_development_scope(
        response["request_id"], "aaron", **scope(root)
    )
    assert prepared["status"] == "ready"
    reader = GovernorCandidateSource(engine.database_path, root)
    try:
        assert reader.next()["candidate_id"] == response["candidate_id"]
    finally:
        reader.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "contents",
    [
        b"def response():\n" + b"#" * MAX_SOURCE_BLOB_BYTES,
        b"def response():\0",
        b"def response():\xff",
    ],
    ids=["oversize", "nul", "invalid_utf8"],
)
async def test_oversize_or_binary_application_blobs_cannot_be_inspected_or_proposed(case, contents):
    engine, root = case
    (root / "app/responder.py").write_bytes(contents)
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "unsupported source",
    )
    assert (await engine.inspect_development_source("response", "app/responder.py"))["files"] == []
    response = await request(engine)
    before = rows(engine, "improvement_candidates")
    with pytest.raises(DevelopmentRequestError, match="source_blob_not_supported"):
        await engine.propose_development_scope(response["request_id"], "aaron", **scope(root))
    assert rows(engine, "improvement_candidates") == before


@pytest.mark.asyncio
async def test_non_owner_and_disabled_requests_never_record_candidates(case):
    engine, _ = case
    with pytest.raises(DevelopmentRequestError, match="owner_request_required"):
        await engine.request_development(str(uuid.uuid4()), "Improve the application", "visitor")
    engine.disabled_file.touch()
    with pytest.raises(DevelopmentRequestError, match="self_improvement_disabled"):
        await request(engine)
    assert rows(engine, "improvement_candidates") == []


def actor(verified=True):
    return UserContext.from_request(
        user_id="aaron",
        user_name="Aaron",
        user_is_admin=True,
        privilege_verified=verified,
        device_id="fixture",
        voice_mode=True,
    )


@pytest.mark.asyncio
async def test_semantic_tools_use_original_text_and_keep_network_retry_identity(case):
    improvement, _ = case
    ai = AIEngine.__new__(AIEngine)
    ai.improvement = improvement
    ai.external_runtime = None
    tools = await ai._openai_tools(
        RoutingDecision(RequestIntent.GENERAL), actor(), "Please improve responsiveness"
    )
    names = {tool["name"] for tool in tools}
    assert {
        "request_development",
        "inspect_development_source",
        "propose_development_scope",
    } <= names
    assert ai._development_tools(actor(False)) == []
    arguments = dict(
        name="request_development",
        arguments_json="{}",
        user_text="model rewritten text",
        authorised_tools=names,
        conversation_id="fixture",
        actor=actor(),
        request_id="turn-1",
        authorization_text="Make Jarvis respond usefully sooner, preserving correctness",
    )
    call = await ai._execute_function(**arguments)
    assert call == await ai._execute_function(**arguments)
    envelope = call["result"]["development_request"]
    failure = await improvement.get_failure(envelope["failure_id"])
    assert failure["evidence"]["source"]["request"] == arguments["authorization_text"]
    rejected = await ai._execute_function(**{**arguments, "actor": actor(False)})
    assert rejected["result"]["error"]["code"] == "owner_authentication_required"
    altered = await ai._execute_function(
        **{**arguments, "arguments_json": '{"request":"invented"}'}
    )
    assert altered["result"]["success"] is False
    events = sanitise_tool_events([call])
    assert events[0]["development_request"] == envelope
    assert "development_request" not in sanitise_tool_events([{**call, "tool": "web_fetch"}])[0]


@pytest.mark.asyncio
async def test_semantic_actual_source_inspection_and_scope_proposal(case):
    improvement, root = case
    ai = AIEngine.__new__(AIEngine)
    ai.improvement = improvement
    ai.external_runtime = None
    names = {tool["name"] for tool in ai._development_tools(actor())}

    async def call(name, arguments):
        return await ai._execute_function(
            name,
            json.dumps(arguments),
            "Improve responses",
            names,
            "conversation",
            actor(),
            request_id="turn",
        )

    requested = (await call("request_development", {}))["result"]["development_request"]
    inspected = await call("inspect_development_source", {"query": "response", "path": None})
    assert inspected["result"]["source"]["source_sha"] == git(root, "rev-parse", "HEAD")
    findings = scope(root)
    findings["source_findings"] = [
        {"path": path, **finding} for path, finding in findings["source_findings"].items()
    ]
    proposed = await call(
        "propose_development_scope", {"request_id": requested["request_id"], **findings}
    )
    assert proposed["result"]["development_request"]["status"] == "ready"
    assert proposed["result"]["development_request"]["candidate_id"] == requested["candidate_id"]


@pytest.mark.asyncio
async def test_real_realtime_turn_publishes_native_owner_request_event(case):
    improvement, _ = case
    envelope = await request(improvement)
    proxy = RealtimeVoiceProxy(
        RealtimeVoiceConfig(
            enabled=True,
            api_key="fixture",
            mobile_token="fixture",
            voice_pe_token="fixture",
            model="fixture",
            voice="marin",
            user_id="aaron",
            user_name="Aaron",
            user_is_admin=True,
            transcription_prompt="Aaron",
        )
    )
    messages = []

    async def send_json(payload):
        messages.append(payload)

    async def brain(command, metadata, on_delta):
        return {
            "success": True,
            "response": "Recorded for review",
            "conversation_id": "fixture",
            "calls": [
                {
                    "tool": "request_development",
                    "result": {
                        "success": True,
                        "development_request": envelope,
                    },
                }
            ],
        }

    await proxy._run_brain_turn(
        1,
        "Improve responses",
        False,
        SimpleNamespace(send_json=send_json),
        SimpleNamespace(send=AsyncMock()),
        brain,
        {"conversation_id": "fixture"},
        "realtime",
        "marin",
        {"generation": 1, "suppress_audio": False},
        client_turn_id=7,
    )
    event = next(message for message in messages if message["type"] == "development.request")
    assert event["development_request"] == envelope
    assert event["generation"] == 1 and event["client_turn_id"] == 7


def api(engine):
    """Mount the actual route/model/guard AST without main's live startup IO."""
    source = Path(__file__).parents[1] / "app/main.py"
    selected = {
        "ImprovementCreateRequest",
        "DevelopmentCreateRequest",
        "DevelopmentScopeRequest",
        "_require_improvement_token",
        "development_request_create",
        "development_request_status",
        "development_request_scope",
    }
    tree = ast.parse(source.read_text())
    module = ast.Module(
        body=[node for node in tree.body if getattr(node, "name", None) in selected],
        type_ignores=[],
    )
    app = FastAPI()
    context = {
        "app": app,
        "improvement": engine,
        "BaseModel": BaseModel,
        "Field": Field,
        "Any": Any,
        "Header": Header,
        "HTTPException": HTTPException,
        "secrets": secrets,
        "settings": SimpleNamespace(jarvis_self_improvement_admin_token="fixture-admin"),
        "DevelopmentRequestError": DevelopmentRequestError,
    }
    exec(compile(module, str(source), "exec"), context)
    return app


@pytest.mark.asyncio
async def test_authenticated_api_real_storage_scope_and_retry(case):
    engine, root = case
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api(engine)), base_url="http://fixture"
    ) as client:
        body = {"request_id": str(uuid.uuid4()), "request": "Improve first useful response"}
        path = "/api/improvement/development"
        assert (await client.post(path, json=body)).status_code == 403
        assert rows(engine, "improvement_candidates") == []
        headers = {"X-Jarvis-Admin-Token": "fixture-admin"}
        response = await client.post(path, json=body, headers=headers)
        assert response.status_code == 200, response.text
        assert (await client.post(path, json=body, headers=headers)).json() == response.json()
        assert (
            await client.post(path, json={**body, "request": "Other request"}, headers=headers)
        ).status_code == 409
        item = f"{path}/{body['request_id']}"
        assert (await client.get(item)).status_code == 403
        assert (await client.post(item + "/scope", json=scope(root))).status_code == 403
        prepared = await client.post(item + "/scope", json=scope(root), headers=headers)
        assert prepared.status_code == 200, prepared.text
        assert prepared.json()["status"] == "ready"
        assert (await client.get(item, headers=headers)).json() == prepared.json()
        assert (
            await client.get(path + "/" + str(uuid.uuid4()), headers=headers)
        ).status_code == 404
