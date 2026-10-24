import copy
import hashlib
import json
import os
import uuid

import pytest

from tools.autonomy_broker import Denied, Request
from tools.autonomy_broker_release import ComposeRelease, compose_argv, fixed_compose
from tools.autonomy_broker_store import Records

OLD_IMAGE = "sha256:" + "a" * 64
NEW_IMAGE = "sha256:" + "b" * 64
OLD_SHA, NEW_SHA = "c" * 40, "d" * 40


@pytest.fixture
def release(tmp_path, monkeypatch):
    from tools import autonomy_broker_release as module

    live = tmp_path / "live"
    live.mkdir()
    (live / "data").mkdir()
    for name in ("docker-compose.yml", "docker-compose.override.yml", ".env"):
        (live / name).write_text("fixture-not-a-credential")
    monkeypatch.setattr(module, "LIVE_ROOT", live)
    protected = tmp_path / "protected"
    protected.mkdir(mode=0o700)
    records = Records(protected, os.getuid(), False)
    policy = {
        "runtime_hashes": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in live.iterdir()
            if p.is_file()
        },
        "core_mounts": {"/app/data": str(live / "data")},
        "core_network": "fixed",
        "core_ports": {},
    }
    current = {
        "Image": OLD_IMAGE,
        "Name": "/jarvis-core",
        "Config": {"Labels": {"org.opencontainers.image.revision": OLD_SHA}},
        "HostConfig": {"IpcMode": "private", "NetworkMode": "fixed", "PortBindings": {}},
        "Mounts": [{"Type": "bind", "Destination": "/app/data", "Source": str(live / "data")}],
    }

    class Docker:
        def inspect(self, name):
            assert name == "jarvis-core"
            return copy.deepcopy(current)

    calls = []
    schemas = {"state.db": {"user_version": 1, "schema_sha256": "e" * 64}}

    def run(argv, fence, **_):
        fence()
        calls.append(argv)
        value = json.loads((protected / ("compose-" + request.request_id + ".json")).read_bytes())[
            "services"
        ]["jarvis-core"]
        current["Image"] = value["image"]
        current["Config"]["Labels"]["org.opencontainers.image.revision"] = value["labels"][
            "org.opencontainers.image.revision"
        ]
        return {"exit_code": 0}

    request = Request(
        {
            "request_id": str(uuid.uuid4()),
            "goal_id": str(uuid.uuid4()),
            "source_sha": NEW_SHA,
            "parameters": {"artifact": NEW_IMAGE},
        }
    )
    engine = ComposeRelease(
        policy,
        records,
        Docker(),
        lambda sha: sha == current["Config"]["Labels"]["org.opencontainers.image.revision"],
        run,
        lambda _: copy.deepcopy(schemas),
    )
    admission = {
        "compatibility": "identical-persistence-runtime-v1",
        "previous_sha": OLD_SHA,
        "previous_image": OLD_IMAGE,
    }
    return engine, request, admission, current, calls, schemas, live


def test_exact_compose_component_without_build_dependencies_or_arbitrary_service(release):
    engine, request, admission, _, calls, _, _ = release
    result = engine.install(request, lambda: None, admission)
    assert result == {"outcome": "installed", "source_sha": NEW_SHA, "data_reverted": False}
    assert calls[0][-7:] == [
        "up",
        "-d",
        "--no-build",
        "--no-deps",
        "--pull",
        "never",
        "jarvis-core",
    ]
    assert "--no-build" in calls[0] and "--no-deps" in calls[0]
    assert "jarvis-speaker-verifier" not in calls[0]
    record = engine.records.read("releases", request.request_id)
    assert record["previous_sha"] == OLD_SHA and record["target_sha"] == NEW_SHA
    assert record["schemas"] and record["health_baseline"] == "healthy"
    before = len(calls)
    with pytest.raises(Denied, match="reconciliation_required"):
        engine.install(request, lambda: None, admission)
    assert len(calls) == before


def test_code_rollback_retains_current_user_data_bytes(release):
    engine, request, admission, _, calls, _, live = release
    data = live / "data/user-state"
    data.write_text("initial")
    engine.install(request, lambda: None, admission)
    data.write_text("new conversations written after install")
    record = engine.records.read("releases", request.request_id)
    result = engine.restore(record, lambda: None)
    assert result["source_sha"] == OLD_SHA and result["data_reverted"] is False
    assert data.read_text() == "new conversations written after install"
    assert len(calls) == 2


def test_schema_change_blocks_rollback_without_reverting_data(release):
    engine, request, admission, _, calls, schemas, live = release
    engine.install(request, lambda: None, admission)
    schemas["state.db"]["user_version"] = 2
    record = engine.records.read("releases", request.request_id)
    with pytest.raises(Denied, match="schema_changed"):
        engine.restore(record, lambda: None)
    assert len(calls) == 1
    assert schemas["state.db"]["user_version"] == 2


def test_unhealthy_candidate_restores_only_code(release):
    engine, request, admission, current, calls, _, _ = release
    original = engine.wait_health

    def unhealthy(sha, fence):
        if sha == NEW_SHA:
            raise Denied("release_health_failed")
        original(sha, fence)

    engine.wait_health = unhealthy
    with pytest.raises(Denied, match="release_health_failed"):
        engine.install(request, lambda: None, admission)
    assert current["Image"] == OLD_IMAGE and len(calls) == 2
    assert engine.records.read("releases", request.request_id)["stage"] == "rolled_back"


def test_stop_during_transition_allows_only_previous_code_compensation(release):
    engine, request, admission, current, calls, _, _ = release

    def fence():
        if calls:
            raise Denied("guardian_not_active")

    with pytest.raises(Denied, match="guardian_not_active"):
        engine.install(request, fence, admission)
    assert current["Image"] == OLD_IMAGE and len(calls) == 2
    assert engine.records.read("releases", request.request_id)["stage"] == "rolled_back"


def test_uncertain_rollback_records_reconciliation_and_does_not_loop(release):
    engine, request, admission, _, calls, _, _ = release
    original = engine.run

    def uncertain(argv, fence, **kwargs):
        original(argv, fence, **kwargs)
        raise Denied("lost_acknowledgement")

    engine.run = uncertain
    with pytest.raises(Denied):
        engine.install(request, lambda: None, admission)
    assert engine.records.read("releases", request.request_id)["stage"] == "reconciliation_required"
    assert len(calls) == 2
    with pytest.raises(Denied):
        engine.install(request, lambda: None, admission)
    assert len(calls) == 2


@pytest.mark.parametrize("change", ["guardian", "runtime", "admission", "previous_image"])
def test_invalid_preflight_never_changes_production(release, change):
    engine, request, admission, current, calls, _, live = release
    fence = lambda: None
    if change == "guardian":

        def fence():
            raise Denied("guardian_not_active")
    elif change == "runtime":
        (live / "docker-compose.yml").write_text("changed")
    elif change == "admission":
        admission["compatibility"] = "trust-the-model"
    else:
        current["Image"] = "sha256:" + "f" * 64
    with pytest.raises(Denied):
        engine.install(request, fence, admission)
    assert calls == []


def test_no_arbitrary_image_or_command_in_override():
    with pytest.raises(Denied):
        fixed_compose("evil --privileged", NEW_SHA)
    value = json.loads(fixed_compose(NEW_IMAGE, NEW_SHA))
    assert set(value["services"]) == {"jarvis-core"}
    assert set(value["services"]["jarvis-core"]) == {"image", "environment", "labels"}
