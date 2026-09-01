#!/usr/bin/env python3
"""Fail closed when a Jarvis release loses a mandatory product capability."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "JARVIS_PRODUCT_BASELINE.json"


def _git(*arguments: str) -> str:
    return subprocess.check_output(
        ["git", *arguments], cwd=ROOT, text=True, stderr=subprocess.STDOUT
    ).strip()


def load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def verify_authoritative_ref(*, allow_dirty: bool = False) -> list[str]:
    errors: list[str] = []
    branch = _git("branch", "--show-current")
    if branch and branch != "jarvis/unified-production":
        errors.append(f"release source branch is {branch!r}, not jarvis/unified-production")
    if not allow_dirty and _git("status", "--porcelain"):
        errors.append("release source worktree is not clean")
    return errors


def verify_assertions(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    assertions = manifest["release_assertions"]
    for relative in assertions["files"]:
        if not (ROOT / relative).is_file():
            errors.append(f"mandatory file missing: {relative}")
    for relative, markers in assertions["markers"].items():
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"marker file missing: {relative}")
            continue
        content = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker not in content:
                errors.append(f"mandatory marker missing from {relative}: {marker}")
    for relative, expected in assertions["sha256"].items():
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"branding file missing: {relative}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            errors.append(f"branding digest changed for {relative}: {actual}")
    exact = assertions.get("exact_source", {})
    reference = exact.get("commit", "")
    for relative in exact.get("files", []):
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"ground-truth source file missing: {relative}")
            continue
        try:
            expected = subprocess.check_output(["git", "show", f"{reference}:{relative}"], cwd=ROOT)
        except subprocess.CalledProcessError:
            errors.append(f"ground-truth source unavailable: {reference}:{relative}")
            continue
        if path.read_bytes() != expected:
            errors.append(f"ground-truth source lineage changed: {relative}")
    for relative in assertions.get("forbidden_files", []):
        if (ROOT / relative).exists():
            errors.append(f"obsolete product file returned: {relative}")
    for relative, markers in assertions.get("forbidden_markers", {}).items():
        path = ROOT / relative
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker in content:
                errors.append(f"obsolete product marker returned in {relative}: {marker}")
    return errors


def verify_android_identity(manifest: dict[str, Any] | None = None) -> list[str]:
    errors: list[str] = []
    manifest = manifest or load_manifest()
    release = manifest["current_release"]
    expected_version = str(release["version_name"])
    expected_code = int(release["version_code"])
    expected_package = str(release["phone_package"])
    build = (ROOT / "android/jarvis-voice-client/app/build.gradle.kts").read_text()
    wear_build = (ROOT / "android/jarvis-voice-client/wear/build.gradle.kts").read_text()
    jarvis_version = (
        ROOT
        / "android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/JarvisVersion.java"
    ).read_text()
    core_version = (ROOT / "bridge/app/version.py").read_text()
    if f'applicationId = "{expected_package}"' not in build:
        errors.append(f"Android applicationId is not {expected_package}")
    if f'applicationId = "{expected_package}"' not in wear_build:
        errors.append(f"Wear applicationId is not {expected_package}")
    match = re.search(r"versionCode\s*=\s*(\d+)", build)
    if match is None or int(match.group(1)) != expected_code:
        errors.append(f"Android versionCode is not current release code {expected_code}")
    required_markers = {
        "Phone build": (build, f'versionName = "{expected_version}"'),
        "Wear build": (wear_build, f'versionName = "{expected_version}"'),
        "Wear versionCode": (wear_build, f"versionCode = {expected_code}"),
        "JarvisVersion": (jarvis_version, f'RELEASE = "{expected_version}"'),
        "Core release": (core_version, f'JARVIS_RELEASE = "{expected_version}"'),
        "Core application version": (
            core_version,
            f'CORE_APPLICATION_VERSION = "{release["core_application_version"]}"',
        ),
        "Core realtime protocol": (
            core_version,
            f"REALTIME_PROTOCOL_VERSION = {release['realtime_protocol']}",
        ),
    }
    for label, (content, marker) in required_markers.items():
        if marker not in content:
            errors.append(f"{label} does not match current release identity: {marker}")
    return errors


def verify_release_workflows(manifest: dict[str, Any] | None = None) -> list[str]:
    errors: list[str] = []
    manifest = manifest or load_manifest()
    obsolete = ROOT / ".github/workflows/android-jarvis-assistant-v18.4.1.yml"
    if obsolete.exists():
        errors.append("obsolete conversation-engine Android release workflow still exists")
    ota = ROOT / ".github/workflows/android-ota-release.yml"
    ota_content = ""
    if not ota.is_file():
        errors.append("authoritative Android OTA workflow is missing")
    else:
        ota_content = ota.read_text(encoding="utf-8")
    if ota.is_file() and "workflow_dispatch:" in ota_content:
        errors.append("OTA publishing must be tag-only, not manually dispatched from a branch")
    elif ota.is_file() and "ota-feeds" in ota_content:
        errors.append("OTA publishing still depends on the obsolete ota-feeds branch")
    if (
        ota.is_file()
        and str(manifest["current_release"]["production_signer_sha256"]) not in ota_content
    ):
        errors.append("OTA workflow production signer does not match the current release baseline")
    if (ROOT / ".github/workflows/wear-v1-signed-pair.yml").exists():
        errors.append("duplicate signed-pair release workflow still exists")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="allow an in-progress local tree while still checking every capability",
    )
    parser.add_argument(
        "--skip-ref",
        action="store_true",
        help="skip local branch/worktree checks (content assertions still run)",
    )
    args = parser.parse_args()
    manifest = load_manifest()
    errors = (
        verify_assertions(manifest)
        + verify_android_identity(manifest)
        + verify_release_workflows(manifest)
    )
    if not args.skip_ref:
        errors += verify_authoritative_ref(allow_dirty=args.allow_dirty)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Jarvis unified product baseline verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
