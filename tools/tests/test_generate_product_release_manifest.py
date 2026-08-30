from pathlib import Path

import pytest

from tools.generate_product_release_manifest import build_manifest


SOURCE = "0123456789abcdef0123456789abcdef01234567"
SIGNER = "a" * 64


def test_manifest_fences_core_phone_and_watch_to_one_source(tmp_path: Path) -> None:
    phone = tmp_path / "phone.apk"
    watch = tmp_path / "watch.apk"
    phone.write_bytes(b"phone")
    watch.write_bytes(b"watch")
    manifest = build_manifest(
        phone=phone,
        watch=watch,
        version="19.0.0-alpha23",
        version_code=190250,
        source_sha=SOURCE,
        signer_sha256=SIGNER,
    )
    assert {
        manifest["jarvisSourceSha"],
        manifest["coreSourceSha"],
        manifest["phoneSourceSha"],
        manifest["watchSourceSha"],
    } == {SOURCE}
    assert manifest["phone"]["file"] == "phone.apk"
    assert manifest["watch"]["file"] == "watch.apk"


@pytest.mark.parametrize("source", ["", "development", "A" * 40, "0" * 39])
def test_manifest_rejects_non_commit_provenance(tmp_path: Path, source: str) -> None:
    phone = tmp_path / "phone.apk"
    watch = tmp_path / "watch.apk"
    phone.write_bytes(b"phone")
    watch.write_bytes(b"watch")
    with pytest.raises(ValueError):
        build_manifest(
            phone=phone,
            watch=watch,
            version="19.0.0-alpha23",
            version_code=190250,
            source_sha=source,
            signer_sha256=SIGNER,
        )
