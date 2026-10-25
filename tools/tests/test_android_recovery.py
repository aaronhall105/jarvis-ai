"""Recovery-specific invariants, not a claim of physical retained-data proof.

Feature-return review must explicitly replace the no-dashboard invariant; do not
silently skip this gate. Storage/key continuity remains required on that return.
"""

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ANDROID = ROOT / "android/jarvis-voice-client"
JAVA = ANDROID / "app/src/main/java/com/aaron/jarvisvoice"


def test_recovery_preserves_alpha40_storage_and_operator_contract():
    # Public source digests, independently compared against the merged alpha40
    # tree. Storage files also match the actual signed alpha39 source.
    expected = {
        "SecureStore.java": "1de4e6ec8155b63fcf377e6192b529fd133eead5d8c4f7446a0da26266754aa2",
        "ChatHistoryStore.java": "2896a223081f2752b4be7bf626a6936939d93380349548c148438e0b2e3f2301",
        "TurnRecoveryJournal.java": "483407d924f12982cdae77ed69ca20f10e57c7129f13e3c9046988f51ede6c9e",
        "AutonomyControl.java": "373421e7698b85a3525905105db0d65bf1eeedfbe7f9a6567b206d0c0abbef22",
    }
    for name, digest in expected.items():
        assert hashlib.sha256((JAVA / name).read_bytes()).hexdigest() == digest


def test_recovery_has_no_dashboard_or_operator_control_entrypoint():
    for name in ("AutonomyActivity", "AutonomyClient", "AutonomyPresentation"):
        assert not (JAVA / f"{name}.java").exists()
        assert name not in (ANDROID / "app/src/main/AndroidManifest.xml").read_text()
    # The unchanged package-private contract has no initialization side effects
    # or calls from recovery production code. Keep its cross-language tests.
    for source in JAVA.rglob("*.java"):
        if source.name != "AutonomyControl.java":
            assert "AutonomyControl" not in source.read_text(), source
    assert (ANDROID / "app/src/test/resources/autonomy_operator_vectors.json").is_file()
    assert (ANDROID / "app/src/test/java/com/aaron/jarvisvoice/AutonomyControlTest.java").is_file()


def test_recovery_identity_is_forward_version_same_package():
    for module in ("app", "wear"):
        build = (ANDROID / module / "build.gradle.kts").read_text()
        assert 'applicationId = "com.aaron.jarvisvoice"' in build
        assert 'versionName = "19.0.0-alpha41"' in build
        assert "versionCode = 190430" in build
    contract = (JAVA / "AutonomyControl.java").read_text()
    assert 'ALIAS = "jarvis.autonomy.operator.v1"' in contract
    assert "if (!store.containsAlias(ALIAS))" in contract
    assert "deleteEntry" not in contract
    assert "getPrivateKey().getEncoded" not in contract
