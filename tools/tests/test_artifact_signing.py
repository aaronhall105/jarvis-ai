"""Root-free signing-boundary tests. No production key, GitHub write or APK install."""

import base64
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from tools import artifact_signing as signing

SOURCE = "a" * 40
HEAD = "b" * 40
MERGE = "c" * 40
TREE = "d" * 40


@pytest.fixture
def env():
    return {
        "SOURCE_SHA": SOURCE,
        "REVIEWED_PR": "98",
        "GITHUB_REPOSITORY": signing.REPO,
        "GITHUB_REPOSITORY_ID": signing.REPO_ID,
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/" + signing.BRANCH,
        "GITHUB_SHA": SOURCE,
        "GITHUB_WORKFLOW_SHA": SOURCE,
        "GITHUB_WORKFLOW_REF": signing.REPO
        + "/.github/workflows/android-artifact-only.yml@refs/heads/"
        + signing.BRANCH,
        "RUNNER_ENVIRONMENT": "github-hosted",
        "GITHUB_RUN_ID": "12345",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_ACTOR_ID": str(signing.OPERATOR_ID),
        "RECIPIENT_CERT_SHA256": "e" * 64,
    }


@pytest.fixture
def responses():
    required = [{"context": n, "app_id": 15368} for n in sorted(signing.CHECKS)]
    checks = lambda sha: {
        "total_count": 4,
        "check_runs": [
            {
                "id": i,
                "name": n,
                "app": {"id": 15368},
                "head_sha": sha,
                "status": "completed",
                "conclusion": "success",
            }
            for i, n in enumerate(sorted(signing.CHECKS), 1)
        ],
    }
    return {
        "/branches/jarvis%2Funified-production": {
            "protected": True,
            "commit": {"sha": SOURCE},
            "protection": {
                "required_status_checks": {"enforcement_level": "everyone", "checks": required}
            },
        },
        "/pulls/98": {
            "merged": True,
            "merge_commit_sha": SOURCE,
            "base": {"ref": signing.BRANCH, "repo": {"id": int(signing.REPO_ID)}},
            "head": {"sha": HEAD, "repo": {"id": int(signing.REPO_ID)}},
        },
        f"/commits/{SOURCE}/check-runs?per_page=100": checks(SOURCE),
        f"/commits/{HEAD}/check-runs?per_page=100": checks(HEAD),
        "/code-scanning/analyses?ref=refs%2Fpull%2F98%2Fmerge&per_page=100": [
            {
                "id": 10,
                "ref": "refs/pull/98/merge",
                "tool": {"name": "CodeQL"},
                "error": "",
                "results_count": 0,
                "commit_sha": MERGE,
            }
        ],
        f"/git/commits/{SOURCE}": {"tree": {"sha": TREE}},
        f"/git/commits/{MERGE}": {"tree": {"sha": TREE}},
        "/code-scanning/alerts?ref=refs%2Fpull%2F98%2Fmerge&state=open&per_page=100": [],
        "/environments/" + signing.ENVIRONMENT: {
            "name": signing.ENVIRONMENT,
            "id": 100,
            "deployment_branch_policy": {
                "protected_branches": True,
                "custom_branch_policies": False,
            },
            "protection_rules": [
                {
                    "type": "required_reviewers",
                    "prevent_self_review": False,
                    "reviewers": [{"type": "User", "reviewer": {"id": signing.OPERATOR_ID}}],
                }
            ],
        },
        "/actions/runs/12345/approvals": [
            {
                "state": "approved",
                "user": {"id": signing.OPERATOR_ID},
                "environments": [{"id": 100}],
            }
        ],
    }


def test_exact_canonical_source_checks_security_and_operator_approval(env, responses):
    result = signing.gate(env, responses.__getitem__, approved=True)
    assert result["sourceSha"] == SOURCE and result["operatorApproved"]
    assert result["sourceTree"] == TREE and result["reviewedPr"] == 98


@pytest.mark.parametrize(
    "field,value",
    [
        ("SOURCE_SHA", "main"),
        ("SOURCE_SHA", "$(anything)"),
        ("SOURCE_SHA", "A" * 40),
        ("REVIEWED_PR", "98/../secrets"),
        ("REVIEWED_PR", "0"),
        ("GITHUB_REPOSITORY", "other/repo"),
        ("GITHUB_REPOSITORY_ID", "1"),
        ("GITHUB_EVENT_NAME", "pull_request"),
        ("GITHUB_EVENT_NAME", "push"),
        ("GITHUB_REF", "refs/heads/feature"),
        ("GITHUB_SHA", HEAD),
        ("GITHUB_WORKFLOW_SHA", HEAD),
        ("GITHUB_WORKFLOW_REF", "untrusted.yml"),
        ("RUNNER_ENVIRONMENT", "self-hosted"),
        ("GITHUB_RUN_ID", "../1"),
        ("GITHUB_RUN_ATTEMPT", "0"),
        ("GITHUB_RUN_ATTEMPT", "2"),
        ("GITHUB_ACTOR_ID", "1"),
        ("RECIPIENT_CERT_SHA256", "not-a-fingerprint"),
    ],
)
def test_context_denied_before_network(env, field, value):
    env[field] = value
    api = Mock(side_effect=AssertionError("must not read metadata"))
    with pytest.raises(signing.Denied):
        signing.gate(env, api)
    api.assert_not_called()


@pytest.mark.parametrize(
    "case",
    [
        "canonical_moved",
        "unprotected",
        "missing_required_check",
        "additional_required_check",
        "admin_bypass",
        "unmerged_pr",
        "wrong_squash",
        "wrong_base",
        "fork_head",
        "failed_check",
        "pending_check",
        "spoofed_app",
        "missing_check",
        "wrong_check_head",
        "truncated_checks",
        "new_failed_check",
        "analysis_missing",
        "analysis_failed",
        "analysis_findings",
        "wrong_analysis_ref",
        "wrong_analysis_tree",
        "open_alert",
        "missing_reviewer",
        "wrong_reviewer",
        "extra_reviewer",
        "unrestricted_environment",
        "no_approval",
        "rejected_approval",
        "wrong_approver",
        "wrong_environment_approval",
    ],
)
def test_all_gate_uncertainty_fails_closed(env, responses, case):
    branch = responses["/branches/jarvis%2Funified-production"]
    pr = responses["/pulls/98"]
    checks = responses[f"/commits/{SOURCE}/check-runs?per_page=100"]
    analyses = responses["/code-scanning/analyses?ref=refs%2Fpull%2F98%2Fmerge&per_page=100"]
    environment = responses["/environments/" + signing.ENVIRONMENT]
    approvals = responses["/actions/runs/12345/approvals"]
    if case == "canonical_moved":
        branch["commit"]["sha"] = HEAD
    elif case == "unprotected":
        branch["protected"] = False
    elif case == "missing_required_check":
        branch["protection"]["required_status_checks"]["checks"].pop()
    elif case == "additional_required_check":
        branch["protection"]["required_status_checks"]["checks"].append(
            {"context": "new-security-gate", "app_id": 15368}
        )
    elif case == "admin_bypass":
        branch["protection"]["required_status_checks"]["enforcement_level"] = "non_admins"
    elif case == "unmerged_pr":
        pr["merged"] = False
    elif case == "wrong_squash":
        pr["merge_commit_sha"] = HEAD
    elif case == "wrong_base":
        pr["base"]["ref"] = "other"
    elif case == "fork_head":
        pr["head"]["repo"]["id"] = 1
    elif case == "failed_check":
        checks["check_runs"][0]["conclusion"] = "failure"
    elif case == "pending_check":
        checks["check_runs"][0]["status"] = "in_progress"
    elif case == "spoofed_app":
        checks["check_runs"][0]["app"]["id"] = 1
    elif case == "missing_check":
        checks["check_runs"].pop()
    elif case == "wrong_check_head":
        checks["check_runs"][0]["head_sha"] = HEAD
    elif case == "truncated_checks":
        checks["total_count"] = 101
    elif case == "new_failed_check":
        newer = copy.deepcopy(checks["check_runs"][0])
        newer.update(id=100, conclusion="failure")
        checks["check_runs"].append(newer)
    elif case == "analysis_missing":
        analyses.clear()
    elif case == "analysis_failed":
        analyses[0]["error"] = "failed"
    elif case == "analysis_findings":
        analyses[0]["results_count"] = 1
    elif case == "wrong_analysis_ref":
        analyses[0]["ref"] = "refs/pull/1/merge"
    elif case == "wrong_analysis_tree":
        responses[f"/git/commits/{MERGE}"]["tree"]["sha"] = HEAD
    elif case == "open_alert":
        responses["/code-scanning/alerts?ref=refs%2Fpull%2F98%2Fmerge&state=open&per_page=100"] = [
            {}
        ]
    elif case == "missing_reviewer":
        environment["protection_rules"].clear()
    elif case == "wrong_reviewer":
        environment["protection_rules"][0]["reviewers"][0]["reviewer"]["id"] = 1
    elif case == "extra_reviewer":
        environment["protection_rules"][0]["reviewers"].append(
            {"type": "User", "reviewer": {"id": 1}}
        )
    elif case == "unrestricted_environment":
        environment["deployment_branch_policy"] = None
    elif case == "no_approval":
        approvals.clear()
    elif case == "rejected_approval":
        approvals[0]["state"] = "rejected"
    elif case == "wrong_approver":
        approvals[0]["user"]["id"] = 1
    elif case == "wrong_environment_approval":
        approvals[0]["environments"][0]["id"] = 999
    else:
        pytest.fail("Unknown regression case")
    with pytest.raises(signing.Denied):
        signing.gate(env, responses.__getitem__, approved=True)


def test_network_metadata_failure_cannot_fall_back(env):
    with pytest.raises(OSError):
        signing.gate(env, Mock(side_effect=OSError("unavailable")))


def test_missing_environment_denies_before_approval_or_signing(env, responses):
    del responses["/environments/" + signing.ENVIRONMENT]
    with pytest.raises(KeyError):
        signing.gate(env, responses.__getitem__)


def test_payload_hash_and_fixed_files(tmp_path, monkeypatch):
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    for name in signing.APKS:
        (incoming / name).write_bytes(b"fixture-apk")
    payload = {
        "sourceSha": SOURCE,
        "versionName": "19.0.0-alpha41",
        "versionCode": 190430,
        "files": {n: signing.file_digest(incoming / n) for n in signing.APKS},
    }
    signing.write_json(incoming / "unsigned.json", payload)
    expected = signing.file_digest(incoming / "unsigned.json")
    monkeypatch.setattr(signing, "source_identity", lambda *_: ("19.0.0-alpha41", 190430))
    monkeypatch.setattr(signing, "verify_apk", lambda *_, **__: None)
    assert signing.validate_payload(tmp_path, incoming, expected, SOURCE) == payload
    with pytest.raises(signing.Denied):
        signing.validate_payload(tmp_path, incoming, "0" * 64, SOURCE)
    (incoming / "phone.apk").write_bytes(b"tampered")
    with pytest.raises(signing.Denied):
        signing.validate_payload(tmp_path, incoming, expected, SOURCE)
    (incoming / "phone.apk").write_bytes(b"fixture-apk")
    (incoming / "extra-command.sh").write_bytes(b"never execute")
    with pytest.raises(signing.Denied):
        signing.validate_payload(tmp_path, incoming, expected, SOURCE)


@pytest.mark.parametrize(
    "kind", ["symlink", "hardlink", "directory", "oversized", "parent_symlink"]
)
def test_artifact_path_and_size_rejected(tmp_path, kind):
    source = tmp_path / "source"
    source.write_bytes(b"public fixture")
    path = tmp_path / "input"
    if kind == "symlink":
        path.symlink_to(source)
    elif kind == "hardlink":
        path.hardlink_to(source)
    elif kind == "directory":
        path.mkdir()
    elif kind == "oversized":
        path.write_bytes(b"large")
    else:
        actual = tmp_path / "actual"
        actual.mkdir()
        (actual / "input").write_bytes(b"public")
        (tmp_path / "linked").symlink_to(actual, target_is_directory=True)
        path = tmp_path / "linked/input"
    with pytest.raises(signing.Denied):
        signing.file_digest(path, 1 if kind == "oversized" else 1024)


@pytest.mark.parametrize(
    "bad", ["package", "version", "code", "debuggable", "certificate", "two_signers"]
)
def test_independent_apk_verification(tmp_path, monkeypatch, bad):
    apk = tmp_path / "phone.apk"
    apk.write_bytes(b"fixture")
    report = "Signer #1 certificate SHA-256 digest: " + signing.CERT
    badging = (
        "package: name='com.aaron.jarvisvoice' versionCode='190430' versionName='19.0.0-alpha41'"
    )
    if bad == "package":
        badging = badging.replace("com.aaron.jarvisvoice", "evil.package")
    elif bad == "version":
        badging = badging.replace("alpha41", "alpha40")
    elif bad == "code":
        badging = badging.replace("190430", "190420")
    elif bad == "debuggable":
        badging += "\napplication-debuggable"
    elif bad == "certificate":
        report = report.replace(signing.CERT, "0" * 64)
    else:
        report += "\nSigner #2 certificate SHA-256 digest: " + signing.CERT
    monkeypatch.setattr(signing, "sdk", lambda name: name)
    monkeypatch.setattr(signing, "run", lambda argv, **_: badging if argv[0] == "aapt" else report)
    with pytest.raises(signing.Denied):
        signing.verify_apk(apk, "19.0.0-alpha41", 190430, signed=True)


def test_no_key_handling_before_approval(tmp_path, monkeypatch, env):
    monkeypatch.setattr(signing, "gate", Mock(side_effect=signing.Denied()))
    key_create = Mock(side_effect=AssertionError("must not create key"))
    monkeypatch.setattr(signing.tempfile, "mkstemp", key_create)
    with pytest.raises(signing.Denied):
        signing.sign(tmp_path, tmp_path / "in", tmp_path / "out", env)
    key_create.assert_not_called()
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("failure", [False, True])
def test_key_cleanup_and_non_publishing_manifest(tmp_path, monkeypatch, env, failure):
    # Dummy bytes, not a signing key. SDK operations are simulated.
    env.update(
        KEYSTORE_BASE64=base64.b64encode(b"dummy-key-not-a-credential").decode(),
        RUNNER_TEMP=str(tmp_path),
        JAVA_HOME="/fixture/java",
        STORE_PASS="fixture",
        KEY_PASS="fixture",
        KEY_ALIAS="fixture",
        PAYLOAD_SHA256="0" * 64,
    )
    evidence = {"sourceSha": SOURCE, "operatorApproved": True}
    monkeypatch.setattr(signing, "gate", lambda *_, **__: evidence)
    monkeypatch.setattr(
        signing,
        "validate_payload",
        lambda *_: {"versionName": "19.0.0-alpha41", "versionCode": 190430},
    )
    monkeypatch.setattr(signing, "verify_apk", lambda *_, **__: None)
    monkeypatch.setattr(signing, "sdk", lambda _: "apksigner")

    def fake_run(argv, **kwargs):
        key = Path(argv[argv.index("--ks") + 1])
        assert key.stat().st_mode & 0o777 == 0o600
        assert set(kwargs["env"]) == {"PATH", "JAVA_HOME", "STORE_PASS", "KEY_PASS"}
        if failure:
            raise signing.Denied()
        Path(argv[argv.index("--out") + 1]).write_bytes(b"simulated-signed-apk")
        return ""

    monkeypatch.setattr(signing, "run", fake_run)
    if failure:
        with pytest.raises(signing.Denied):
            signing.sign(tmp_path, tmp_path / "in", tmp_path / "out", env)
        assert not (tmp_path / "out/artifact-only-manifest.json").exists()
    else:
        signing.sign(tmp_path, tmp_path / "in", tmp_path / "out", env)
        result = json.loads((tmp_path / "out/artifact-only-manifest.json").read_text())
        assert (
            result["artifactOnly"]
            and not result["publicationAuthorized"]
            and not result["installationAuthorized"]
        )
        assert "channel" not in result and "downloadUrl" not in result
        assert set(p.name for p in (tmp_path / "out").iterdir()) == {
            "phone.apk",
            "wear.apk",
            "artifact-only-manifest.json",
        }
    assert list(tmp_path.glob("*.jks")) == []


def test_main_never_exposes_provider_or_secret_error(monkeypatch, capsys):
    monkeypatch.setattr(signing.sys, "argv", ["signer", "gate"])
    monkeypatch.setattr(signing, "gate", Mock(side_effect=RuntimeError("private-secret-value")))
    assert signing.main() == 1
    assert "private-secret-value" not in capsys.readouterr().err


def test_tools_never_inherit_signing_or_github_secrets(monkeypatch):
    for key in ("KEYSTORE_BASE64", "GH_TOKEN", "STORE_PASS", "KEY_PASS", "LD_PRELOAD"):
        monkeypatch.setenv(key, "fixture-not-a-secret")
    child = Mock(return_value=Mock(returncode=0, stdout="ok"))
    monkeypatch.setattr(signing.subprocess, "run", child)
    assert signing.run(["git", "rev-parse", "HEAD"]) == "ok"
    assert set(child.call_args.kwargs["env"]) == {"PATH", "LANG", "JAVA_HOME"}
    assert child.call_args.kwargs["timeout"] == 120


@pytest.mark.parametrize(
    "field,value",
    [
        ("version_name", "19.0.0-alpha41; shell"),
        ("version_code", 190420),
        ("version_code", True),
        ("phone_package", "other.package"),
        ("production_signer_sha256", "0" * 64),
    ],
)
def test_source_release_identity_rejected(tmp_path, monkeypatch, field, value):
    (tmp_path / "docs").mkdir()
    release = {
        "version_name": "19.0.0-alpha41",
        "version_code": 190430,
        "phone_package": signing.PACKAGE,
        "production_signer_sha256": signing.CERT,
    }
    release[field] = value
    signing.write_json(tmp_path / "docs/JARVIS_PRODUCT_BASELINE.json", {"current_release": release})
    monkeypatch.setattr(signing, "run", Mock(side_effect=[SOURCE, ""]))
    with pytest.raises(signing.Denied):
        signing.source_identity(tmp_path, SOURCE)


def test_api_is_fixed_host_get_and_refuses_redirects(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "fixture")
    response = Mock()
    response.__enter__ = Mock(return_value=Mock(read=Mock(return_value=b"{}")))
    response.__exit__ = Mock(return_value=False)
    opener = Mock(open=Mock(return_value=response))
    monkeypatch.setattr(signing.urllib.request, "build_opener", Mock(return_value=opener))
    assert signing.get_api("/pulls/98") == {}
    request = opener.open.call_args.args[0]
    assert request.full_url == "https://api.github.com/repos/aaronhall105/jarvis-ai/pulls/98"
    assert request.get_method() == "GET"
    assert opener.open.call_args.kwargs["timeout"] == 20
    with pytest.raises(signing.Denied):
        signing.get_api("//evil.example/")
    with pytest.raises(signing.Denied):
        signing.NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example")


def test_workflow_is_manual_read_only_separated_and_artifact_only():
    root = Path(__file__).resolve().parents[2]
    raw = (root / ".github/workflows/android-artifact-only.yml").read_text()
    workflow = yaml.load(raw, Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert set(workflow["on"]["workflow_dispatch"]["inputs"]) == {
        "source_sha",
        "reviewed_pr",
        "recipient_cert_sha256",
    }
    assert workflow["permissions"] == {
        k: "read" for k in ("contents", "actions", "checks", "pull-requests", "security-events")
    }
    assert set(workflow["jobs"]) == {"build", "sign"}
    build, sign = workflow["jobs"]["build"], workflow["jobs"]["sign"]
    assert "secrets." not in json.dumps(build)
    assert sign["needs"] == "build"
    assert sign["environment"] == {"name": signing.ENVIRONMENT, "deployment": "false"}
    assert all(j["runs-on"] == "ubuntu-latest" and "permissions" not in j for j in (build, sign))
    # Only these reviewed actions may execute in the credential-bearing job.
    assert [s.get("uses", s.get("name")) for s in sign["steps"]] == [
        "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803",
        "actions/setup-java@b6effb05e454b25005698d916606bdc6ffcbf961",
        "android-actions/setup-android@9fc6c4e9069bf8d3d10b2204b1fb8f6ef7065407",
        "Install fixed verifier/signing SDK",
        "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093",
        "Revalidate approval/source/bytes, sign and independently verify",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    ]
    assert sign["steps"][5]["run"] == "python3 -m tools.artifact_signing sign"
    assert sign["steps"][6]["with"]["path"].splitlines() == [
        "private-artifacts/phone-wear.cms",
        "private-artifacts/receipt.json",
    ]
    assert sign["steps"][6]["with"]["overwrite"] == "false"
    assert sign["steps"][4]["with"]["artifact-ids"] == "${{ needs.build.outputs.artifact_id }}"
    assert sign["steps"][5]["env"]["PAYLOAD_SHA256"] == "${{ needs.build.outputs.payload_sha256 }}"
    assert all(
        s.get("with", {}).get("persist-credentials") == "false"
        for j in (build, sign)
        for s in j["steps"]
        if s.get("uses", "").startswith("actions/checkout@")
    )
    for forbidden in (
        "gh release",
        "git push",
        "refs/tags",
        "jarvis-alpha-feed",
        "adb ",
        "deploy_unified_core",
        "developer_gateway/install.sh",
        "systemctl",
        "pm clear",
    ):
        assert forbidden not in raw
    # Original publication workflow remains distinct; this file has no mode that
    # can turn artifact staging into publication.
    assert "publish" not in workflow["on"]["workflow_dispatch"]["inputs"]
