"""Pin the recovery source to published Alpha43 and validate Alpha45 metadata."""

import hashlib
from pathlib import Path
import subprocess

from tools.verify_product_baseline import load_manifest

ROOT = Path(__file__).resolve().parents[2]
ANDROID = "android/jarvis-voice-client/"
VERSION_FILES = {
    "app/build.gradle.kts",
    "wear/build.gradle.kts",
    "app/src/main/java/com/aaron/jarvisvoice/JarvisVersion.java",
    "app/src/test/java/com/aaron/jarvisvoice/RealtimeProtocolAlpha15Test.java",
    "app/src/test/java/com/aaron/jarvisvoice/UpdateVersionAlpha15Test.java",
}


def test_recovery_retains_entire_published_alpha43_android_tree():
    # Independently compute from the immutable published Alpha43 commit. The
    # recovery candidate may evolve after Alpha43; it must not rewrite the
    # published source used as the recovery baseline.
    published = "4812ea7a2b83188cedfdf7c3631c70db2ca422a8"
    paths = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", published, "--", ANDROID], cwd=ROOT, text=True
    ).splitlines()
    assert len(paths) == 319
    digest = hashlib.sha256()
    for relative in paths:
        content = subprocess.check_output(["git", "show", f"{published}:{relative}"], cwd=ROOT)
        if relative.removeprefix(ANDROID) in VERSION_FILES:
            # The published tree is already Alpha43; keep the replacement
            # normalization to make the invariant explicit and deterministic.
            content = content.replace(b"19.0.0-alpha44", b"19.0.0-alpha43")
            content = content.replace(b"190460", b"190450")
        digest.update(relative.encode() + b"\0" + hashlib.sha256(content).digest())
    assert digest.hexdigest() == (
        "d3d6618da923931a8797a2c349a44a0e4c4c1cca4e9b42a45e2fefbb76bab068"
    )


def test_recovery_has_new_monotonic_identity_and_matching_release_notes():
    release = load_manifest()["current_release"]
    assert release["version_name"] == "19.0.0-alpha46"
    assert release["version_code"] == 190480
    assert release["version_code"] > max(190420, 190430, 190440, 190450, 190460, 190470)
    assert release["phone_package"] == "com.aaron.jarvisvoice"
    for component in ("app", "wear"):
        gradle = (ROOT / ANDROID / component / "build.gradle.kts").read_text()
        assert 'applicationId = "com.aaron.jarvisvoice"' in gradle
    notes = (ROOT / "docs/releases/CHANGES_V19_0_0_ALPHA46.md").read_text()
    assert "190480" in notes
    assert "No storage migration" in notes
