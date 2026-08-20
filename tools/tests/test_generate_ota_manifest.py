import hashlib
import importlib.util
from pathlib import Path
import pytest

MODULE_PATH = Path(__file__).parents[1] / "generate_ota_manifest.py"
SPEC = importlib.util.spec_from_file_location("generate_ota_manifest", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MODULE)

def test_manifest_uses_exact_artifact(tmp_path: Path) -> None:
    apk = tmp_path / "app.apk"; apk.write_bytes(b"tested-apk")
    result = MODULE.generate(apk, "19.0.0-alpha15", 190150, "a" * 40, "Release notes", "2026-08-20T12:00:00Z")
    assert result["channel"] == "alpha"
    assert result["versionName"] == "19.0.0-alpha15" and result["versionCode"] == 190150
    assert result["commitSha"] == "a" * 40
    assert result["sha256"] == hashlib.sha256(b"tested-apk").hexdigest()
    assert result["apkSize"] == len(b"tested-apk")
    assert "19.0.0-alpha15" in result["apkUrl"]

@pytest.mark.parametrize("version,expected", [("19.0.0-alpha16", "alpha"), ("19.0.0-beta1", "beta"), ("19.0.0", "stable")])
def test_channel(version: str, expected: str) -> None: assert MODULE.channel(version) == expected

def test_invalid_inputs_rejected(tmp_path: Path) -> None:
    apk = tmp_path / "empty.apk"; apk.write_bytes(b"")
    with pytest.raises(ValueError): MODULE.generate(apk, "19.0.0-alpha15", 190150, "a" * 40, "notes", "2026-08-20T12:00:00Z")
    with pytest.raises(ValueError): MODULE.channel("19.0.0-nightly1")
