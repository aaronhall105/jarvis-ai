"""Fail-closed, canonical-only APK staging. No publication or deployment API.

Runs only from the reviewed canonical workflow on disposable GitHub-hosted runners.
Builds never receive signing secrets. The separate signer executes SDK tools, not
Gradle, APK contents or candidate-supplied programs. No local signing entrypoint is
authorized by this script; context, source, checks and human approval are required.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any
import urllib.parse
import urllib.request

from tools.generate_product_release_manifest import build_manifest

REPO = "aaronhall105/jarvis-ai"
REPO_ID = "1309251679"
OPERATOR_ID = 291914934
BRANCH = "jarvis/unified-production"
ENVIRONMENT = "jarvis-artifact-signing"
CERT = "009fd523f27cf94eb98917e17670804897e6378e5eccf1ce3ead680721691aac"
PACKAGE = "com.aaron.jarvisvoice"
CHECKS = {"core-tests", "quality-security", "android-unit-tests", "Analyze Python"}
SHA = re.compile(r"[0-9a-f]{40}")
HASH = re.compile(r"[0-9a-f]{64}")
APK_LIMIT = 150_000_000
APKS = {"phone.apk": "app", "wear.apk": "wear"}


class Denied(RuntimeError):
    pass


def require(condition: Any) -> None:
    if not condition:
        raise Denied("Artifact-only safety gate refused")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Denied("API redirect refused")


def get_api(path: str) -> Any:
    # No host/URL is caller-selectable; all call sites are GET metadata reads.
    require(path.startswith("/") and not path.startswith("//"))
    token = os.environ.get("GH_TOKEN", "")
    require(token)
    request = urllib.request.Request(
        "https://api.github.com/repos/" + REPO + path,
        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json"},
        method="GET",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=20) as response:
        raw = response.read(4_000_001)
    require(len(raw) <= 4_000_000)
    return json.loads(raw)


def context(env: dict[str, str]) -> tuple[str, int]:
    source = env.get("SOURCE_SHA", "")
    pr = env.get("REVIEWED_PR", "")
    require(SHA.fullmatch(source) and re.fullmatch(r"[1-9][0-9]{0,8}", pr))
    require(env.get("GITHUB_REPOSITORY") == REPO)
    require(env.get("GITHUB_REPOSITORY_ID") == REPO_ID)
    require(env.get("GITHUB_EVENT_NAME") == "workflow_dispatch")
    require(env.get("GITHUB_REF") == "refs/heads/" + BRANCH)
    require(env.get("GITHUB_SHA") == source == env.get("GITHUB_WORKFLOW_SHA"))
    require(env.get("RUNNER_ENVIRONMENT") == "github-hosted")
    require(
        env.get("GITHUB_WORKFLOW_REF")
        == REPO + "/.github/workflows/android-artifact-only.yml@refs/heads/" + BRANCH
    )
    require(re.fullmatch(r"[1-9][0-9]*", env.get("GITHUB_RUN_ID", "")))
    require(re.fullmatch(r"[1-9][0-9]*", env.get("GITHUB_RUN_ATTEMPT", "")))
    return source, int(pr)


def checks_pass(source: str, api) -> list[int]:
    data = api(f"/commits/{source}/check-runs?per_page=100")
    require(data["total_count"] <= 100)  # Ambiguous/truncated evidence fails closed.
    latest: dict[str, dict] = {}
    for run in data["check_runs"]:
        name = run["name"]
        if name in CHECKS and run["app"]["id"] == 15368:
            require(run["head_sha"] == source)
            if name not in latest or run["id"] > latest[name]["id"]:
                latest[name] = run
    require(set(latest) == CHECKS)
    require(
        all(r["status"] == "completed" and r["conclusion"] == "success" for r in latest.values())
    )
    return sorted(r["id"] for r in latest.values())


def gate(env: dict[str, str], api=get_api, *, approved: bool = False) -> dict:
    source, pr = context(env)
    branch = api("/branches/" + urllib.parse.quote(BRANCH, safe=""))
    require(branch["protected"] is True and branch["commit"]["sha"] == source)
    protection = branch["protection"]["required_status_checks"]
    require(protection["enforcement_level"] == "everyone")
    protected = {c["context"] for c in protection["checks"] if c["app_id"] == 15368}
    require(CHECKS <= protected)
    pull = api(f"/pulls/{pr}")
    require(pull["merged"] is True and pull["merge_commit_sha"] == source)
    require(pull["base"]["ref"] == BRANCH and str(pull["base"]["repo"]["id"]) == REPO_ID)
    require(str(pull["head"]["repo"]["id"]) == REPO_ID and SHA.fullmatch(pull["head"]["sha"]))
    source_checks = checks_pass(source, api)
    pr_checks = checks_pass(pull["head"]["sha"], api)
    ref = urllib.parse.quote(f"refs/pull/{pr}/merge", safe="")
    analyses = api(f"/code-scanning/analyses?ref={ref}&per_page=100")
    require(isinstance(analyses, list) and 0 < len(analyses) < 100)
    python_analyses = [a for a in analyses if a["tool"]["name"] == "CodeQL"]
    require(python_analyses)
    analysis = max(python_analyses, key=lambda a: a["id"])
    require(not analysis["error"] and analysis["results_count"] == 0)
    require(analysis["ref"] == f"refs/pull/{pr}/merge" and SHA.fullmatch(analysis["commit_sha"]))
    tree = api(f"/git/commits/{source}")["tree"]["sha"]
    require(tree == api(f"/git/commits/{analysis['commit_sha']}")["tree"]["sha"])
    require(api(f"/code-scanning/alerts?ref={ref}&state=open&per_page=100") == [])
    environment = api("/environments/" + ENVIRONMENT)
    require(environment["name"] == ENVIRONMENT)
    require(
        environment["deployment_branch_policy"]
        == {
            "protected_branches": True,
            "custom_branch_policies": False,
        }
    )
    reviewers = [r for r in environment["protection_rules"] if r["type"] == "required_reviewers"]
    require(len(reviewers) == 1)
    require(
        [(r["type"], r["reviewer"]["id"]) for r in reviewers[0]["reviewers"]]
        == [("User", OPERATOR_ID)]
    )
    if approved:
        # A bypass/start is not enough: require the operator's explicit approval
        # record for this run/environment as well as the native job approval gate.
        approvals = api(f"/actions/runs/{env['GITHUB_RUN_ID']}/approvals")
        relevant = [
            a for a in approvals if any(e["id"] == environment["id"] for e in a["environments"])
        ]
        require(
            relevant
            and all(a["state"] == "approved" and a["user"]["id"] == OPERATOR_ID for a in relevant)
        )
    return {
        "sourceSha": source,
        "sourceTree": tree,
        "reviewedPr": pr,
        "prHeadSha": pull["head"]["sha"],
        "codeqlAnalysisId": analysis["id"],
        "sourceChecks": source_checks,
        "prChecks": pr_checks,
        "workflowSha": env["GITHUB_WORKFLOW_SHA"],
        "runId": env["GITHUB_RUN_ID"],
        "runAttempt": env["GITHUB_RUN_ATTEMPT"],
        "operatorApproved": approved,
    }


def source_identity(root: Path, source: str) -> tuple[str, int]:
    actual = run(["git", "rev-parse", "HEAD"], cwd=root).strip()
    require(actual == source)
    run(["git", "diff", "--exit-code", "HEAD", "--"], cwd=root)
    release = json.loads((root / "docs/JARVIS_PRODUCT_BASELINE.json").read_text())[
        "current_release"
    ]
    version, code = release["version_name"], release["version_code"]
    match = re.fullmatch(r"19\.0\.0-alpha([1-9][0-9]*)", version)
    require(match and type(code) is int and code == 190020 + 10 * int(match[1]))
    require(release["phone_package"] == PACKAGE and release["production_signer_sha256"] == CERT)
    return version, code


def tool_env() -> dict[str, str]:
    return {"PATH": os.defpath, "LANG": "C.UTF-8", "JAVA_HOME": os.environ.get("JAVA_HOME", "")}


def run(argv: list[str], *, cwd: Path | None = None, env: dict | None = None) -> str:
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=tool_env() if env is None else env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    require(result.returncode == 0)  # Never echo tools' potentially sensitive stderr.
    return result.stdout


def file_digest(path: Path, limit: int = APK_LIMIT) -> str:
    require(not any(p.is_symlink() for p in (path, *path.parents)))
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 < info.st_size <= limit)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sdk(name: str) -> str:
    require(name in {"aapt", "apksigner"})
    return str(Path(os.environ["ANDROID_HOME"]) / "build-tools/36.0.0" / name)


def verify_apk(path: Path, version: str, code: int, *, signed: bool) -> None:
    file_digest(path)
    badging = run([sdk("aapt"), "dump", "badging", str(path)])
    require(f"package: name='{PACKAGE}' versionCode='{code}' versionName='{version}'" in badging)
    require("application-debuggable" not in badging)
    if signed:
        report = run([sdk("apksigner"), "verify", "--verbose", "--print-certs", str(path)])
        certificates = re.findall(r"Signer #[0-9]+ certificate SHA-256 digest: ([0-9a-f]+)", report)
        require(certificates == [CERT])
    else:
        result = subprocess.run(
            [sdk("apksigner"), "verify", str(path)],
            env=tool_env(),
            capture_output=True,
            timeout=120,
        )
        require(result.returncode != 0)  # Build job may not silently sign.


def write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")


def record(root: Path, directory: Path, evidence: dict) -> str:
    version, code = source_identity(root, evidence["sourceSha"])
    require(set(p.name for p in directory.iterdir()) == set(APKS))
    payload = {
        "sourceSha": evidence["sourceSha"],
        "versionName": version,
        "versionCode": code,
        "files": {},
    }
    for name in APKS:
        verify_apk(directory / name, version, code, signed=False)
        payload["files"][name] = file_digest(directory / name)
    write_json(directory / "unsigned.json", payload)
    return file_digest(directory / "unsigned.json", 8192)


def validate_payload(root: Path, directory: Path, expected: str, source: str) -> dict:
    require(HASH.fullmatch(expected))
    require(set(p.name for p in directory.iterdir()) == {*APKS, "unsigned.json"})
    require(file_digest(directory / "unsigned.json", 8192) == expected)
    payload = json.loads((directory / "unsigned.json").read_text())
    require(set(payload) == {"sourceSha", "versionName", "versionCode", "files"})
    version, code = source_identity(root, source)
    require(
        (payload["sourceSha"], payload["versionName"], payload["versionCode"])
        == (source, version, code)
    )
    require(set(payload["files"]) == set(APKS))
    for name in APKS:
        require(file_digest(directory / name) == payload["files"][name])
        verify_apk(directory / name, version, code, signed=False)
    return payload


def sign(root: Path, incoming: Path, outgoing: Path, env: dict[str, str]) -> None:
    evidence = gate(env, approved=True)  # Recheck after approval, before key handling.
    payload = validate_payload(root, incoming, env["PAYLOAD_SHA256"], evidence["sourceSha"])
    require(not outgoing.exists())
    outgoing.mkdir(mode=0o700)
    encoded = env["KEYSTORE_BASE64"]
    require(0 < len(encoded) < 2_000_000)
    key_bytes = base64.b64decode(encoded, validate=True)
    require(key_bytes)
    fd, key_name = tempfile.mkstemp(
        prefix="jarvis-artifact-only-", suffix=".jks", dir=env["RUNNER_TEMP"]
    )
    key = Path(key_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(key_bytes)
        signer_env = {
            "PATH": os.defpath,
            "JAVA_HOME": env["JAVA_HOME"],
            "STORE_PASS": env["STORE_PASS"],
            "KEY_PASS": env["KEY_PASS"],
        }
        for name in APKS:
            run(
                [
                    sdk("apksigner"),
                    "sign",
                    "--ks",
                    str(key),
                    "--ks-key-alias",
                    env["KEY_ALIAS"],
                    "--ks-pass",
                    "env:STORE_PASS",
                    "--key-pass",
                    "env:KEY_PASS",
                    "--v1-signing-enabled",
                    "true",
                    "--v2-signing-enabled",
                    "true",
                    "--v3-signing-enabled",
                    "true",
                    "--v4-signing-enabled",
                    "false",
                    "--out",
                    str(outgoing / name),
                    str(incoming / name),
                ],
                env=signer_env,
            )
            verify_apk(outgoing / name, payload["versionName"], payload["versionCode"], signed=True)
        # Recheck source/check/approval evidence once more before artifact upload.
        require(gate(env, approved=True) == evidence)
        manifest = build_manifest(
            phone=outgoing / "phone.apk",
            watch=outgoing / "wear.apk",
            version=payload["versionName"],
            version_code=payload["versionCode"],
            source_sha=evidence["sourceSha"],
            signer_sha256=CERT,
        )
        manifest.update(
            {
                "artifactOnly": True,
                "publicationAuthorized": False,
                "installationAuthorized": False,
                "evidence": evidence,
                "unsignedManifestSha256": env["PAYLOAD_SHA256"],
            }
        )
        write_json(outgoing / "artifact-only-manifest.json", manifest)
    finally:
        key.unlink(missing_ok=True)


def main() -> int:
    try:
        require(len(sys.argv) == 2 and sys.argv[1] in {"gate", "record", "sign"})
        env = dict(os.environ)
        root = Path.cwd()
        if sys.argv[1] == "sign":
            sign(root, root / "incoming", root / "signed-artifacts", env)
        else:
            evidence = gate(env)
            if sys.argv[1] == "record":
                digest = record(root, root / "unsigned-artifacts", evidence)
                with Path(env["GITHUB_OUTPUT"]).open("a") as output:
                    output.write("payload_sha256=" + digest + "\n")
        print("Artifact-only validation complete; no publication or installation")
        return 0
    except Exception:
        print(
            "Artifact-only operation refused; inspect non-secret checks and approval state",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
