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
ALPHA17_ANDROID_COMMIT = "3279a57ac8593cc2eba70d1678350dea25235a96"
ALPHA17_EXACT_FILES = (
    "android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/ChatHistoryActivity.java",
    "android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/ImprovementsActivity.java",
    "android/jarvis-voice-client/app/src/main/java/com/aaron/jarvisvoice/ProactiveActivity.java",
    "android/jarvis-voice-client/app/src/main/res/drawable-nodpi/ic_launcher_foreground.png",
    "android/jarvis-voice-client/app/src/main/res/drawable-nodpi/jarvis_logo_ui.png",
    "android/jarvis-voice-client/app/src/main/res/drawable/ic_jarvis.xml",
    "android/jarvis-voice-client/app/src/main/res/drawable/ic_jarvis_status.xml",
    "android/jarvis-voice-client/app/src/main/res/drawable/ic_launcher_foreground.xml",
    "android/jarvis-voice-client/app/src/main/res/drawable/ic_launcher_monochrome.xml",
    "android/jarvis-voice-client/app/src/main/res/mipmap-anydpi-v33/ic_launcher.xml",
    "android/jarvis-voice-client/app/src/main/res/mipmap-anydpi-v33/ic_launcher_round.xml",
    "android/jarvis-voice-client/app/src/main/res/values/colors.xml",
)


def _git(*arguments: str) -> str:
    return subprocess.check_output(
        ["git", *arguments], cwd=ROOT, text=True, stderr=subprocess.STDOUT
    ).strip()


def load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def verify_alpha17_android_lineage() -> list[str]:
    errors: list[str] = []
    for relative in ALPHA17_EXACT_FILES:
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"alpha17 Android baseline file missing: {relative}")
            continue
        try:
            reference = subprocess.check_output(
                ["git", "show", f"{ALPHA17_ANDROID_COMMIT}:{relative}"],
                cwd=ROOT,
                stderr=subprocess.STDOUT,
            )
        except subprocess.CalledProcessError:
            errors.append(f"alpha17 Android source unavailable for comparison: {relative}")
            continue
        if path.read_bytes() != reference:
            errors.append(f"alpha17 Android baseline bytes changed: {relative}")
    return errors


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
    for relative in assertions.get("absent_files", []):
        if (ROOT / relative).exists():
            errors.append(f"obsolete product file must be absent: {relative}")
    for relative, markers in assertions["markers"].items():
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"marker file missing: {relative}")
            continue
        content = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker not in content:
                errors.append(f"mandatory marker missing from {relative}: {marker}")
    for relative, markers in assertions.get("forbidden_markers", {}).items():
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"forbidden-marker file missing: {relative}")
            continue
        content = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker in content:
                errors.append(f"obsolete product marker present in {relative}: {marker}")
    for relative, expected in assertions["sha256"].items():
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"branding file missing: {relative}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            errors.append(f"branding digest changed for {relative}: {actual}")
    return errors


def verify_android_identity() -> list[str]:
    errors: list[str] = []
    build = (ROOT / "android/jarvis-voice-client/app/build.gradle.kts").read_text()
    if 'applicationId = "com.aaron.jarvisvoice"' not in build:
        errors.append("Android applicationId is not com.aaron.jarvisvoice")
    match = re.search(r"versionCode\s*=\s*(\d+)", build)
    if match is None or int(match.group(1)) <= 190230:
        errors.append("Android versionCode is not newer than alpha21")
    return errors


def verify_release_workflows() -> list[str]:
    errors: list[str] = []
    obsolete = ROOT / ".github/workflows/android-jarvis-assistant-v18.4.1.yml"
    if obsolete.exists():
        errors.append("obsolete conversation-engine Android release workflow still exists")
    ota = ROOT / ".github/workflows/android-ota-release.yml"
    if not ota.is_file():
        errors.append("authoritative Android OTA workflow is missing")
    elif "workflow_dispatch:" in ota.read_text(encoding="utf-8"):
        errors.append("OTA publishing must be tag-only, not manually dispatched from a branch")
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
        + verify_alpha17_android_lineage()
        + verify_android_identity()
        + verify_release_workflows()
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
