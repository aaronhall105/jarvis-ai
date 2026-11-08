"""Release-specific storage/identity review pins, not physical upgrade proof."""

import hashlib
from pathlib import Path

import pytest

from tools.verify_product_baseline import load_manifest, verify_android_identity

ROOT = Path(__file__).resolve().parents[2]
PHONE = "android/jarvis-voice-client/app/src/main/"
ALPHA40_SOURCE = "d4013a0"


def _historical_bytes(commit: str, relative: str) -> bytes:
    """Read the immutable release source, never the candidate working tree."""
    import subprocess

    return subprocess.check_output(["git", "show", f"{commit}:{relative}"], cwd=ROOT)


def test_candidate_is_above_published_and_reserved_recovery_versions():
    release = load_manifest()["current_release"]
    assert release["version_name"] == "19.0.0-alpha45"
    # Published alpha40/43; PR98 alpha41; documented alpha42 return plan.
    assert release["version_code"] == 190470 > max(190420, 190430, 190440, 190450, 190460)
    assert release["phone_package"] == "com.aaron.jarvisvoice"
    assert release["production_signer_sha256"] == (
        "009fd523f27cf94eb98917e17670804897e6378e5eccf1ce3ead680721691aac"
    )
    assert release["core_application_version"] == "3.7.0"
    assert release["realtime_protocol"] == 2
    assert verify_android_identity() == []


@pytest.mark.parametrize(
    "relative,digest",
    [
        (
            PHONE + "java/com/aaron/jarvisvoice/SecureStore.java",
            "1de4e6ec8155b63fcf377e6192b529fd133eead5d8c4f7446a0da26266754aa2",
        ),
        (
            PHONE + "java/com/aaron/jarvisvoice/ChatHistoryStore.java",
            "2896a223081f2752b4be7bf626a6936939d93380349548c148438e0b2e3f2301",
        ),
        (
            PHONE + "java/com/aaron/jarvisvoice/TurnRecoveryJournal.java",
            "483407d924f12982cdae77ed69ca20f10e57c7129f13e3c9046988f51ede6c9e",
        ),
        (
            PHONE + "java/com/aaron/jarvisvoice/AutonomyControl.java",
            "373421e7698b85a3525905105db0d65bf1eeedfbe7f9a6567b206d0c0abbef22",
        ),
        (
            PHONE + "AndroidManifest.xml",
            "d05219f10e9289dea39d5703d9b5371a6b056aead98b47d08eb05d87e6f9eee4",
        ),
        (
            "android/jarvis-voice-client/wear/src/main/AndroidManifest.xml",
            "60f79465dd0c5dc8322d5dce94c39628d496c89d137f02cab76b3c469d0383c3",
        ),
    ],
)
def test_alpha40_storage_keys_and_component_contract_remain_unchanged(relative, digest):
    # Compare the published Alpha40 commit itself. Candidate work is allowed to
    # add a domain-separated owner-control operation without rewriting history.
    assert hashlib.sha256(_historical_bytes(ALPHA40_SOURCE, relative)).hexdigest() == digest
