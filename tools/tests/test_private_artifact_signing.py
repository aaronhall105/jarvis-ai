"""Real disposable encryption; signing is mocked and no production key is used."""

import hashlib
import json
from pathlib import Path
import ssl
import tarfile
from unittest.mock import Mock

import pytest

from tools import artifact_signing as signing


@pytest.fixture(scope="module")
def recipient(tmp_path_factory):
    root = tmp_path_factory.mktemp("recipient")
    key, cert = root / "fixture.key", root / "fixture.pem"
    signing.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:3072",
            "-nodes",
            "-days",
            "2",
            "-subj",
            "/CN=Disposable encryption fixture",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ]
    )
    pem = cert.read_text()
    return (
        key,
        cert,
        {
            "RECIPIENT_CERT_PEM": pem,
            "RECIPIENT_CERT_SHA256": hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest(),
        },
    )


def test_real_encrypted_bundle_round_trip_and_tamper_rejection(tmp_path, recipient):
    key, cert, env = recipient
    verified = signing.recipient_certificate(tmp_path, env)
    assert verified.read_text() == cert.read_text()
    signed = tmp_path / "signed"
    signed.mkdir()
    files = {
        "phone.apk": b"fixture-phone-not-an-apk",
        "wear.apk": b"fixture-wear-not-an-apk",
        "artifact-only-manifest.json": b'{"fixture":true}',
    }
    for name, data in files.items():
        (signed / name).write_bytes(data)
    output = tmp_path / "encrypted"
    signing.seal_signed_bundle(signed, output, verified, {"sourceSha": "a" * 40})
    assert {p.name for p in output.iterdir()} == {"phone-wear.cms", "receipt.json"}
    ciphertext = output / "phone-wear.cms"
    assert all(data not in ciphertext.read_bytes() for data in files.values())
    receipt = json.loads((output / "receipt.json").read_text())
    assert receipt["ciphertextSha256"] == signing.file_digest(ciphertext)
    assert not receipt["publicationAuthorized"] and not receipt["installationAuthorized"]
    restored = tmp_path / "restored.tar"
    command = [
        "openssl",
        "cms",
        "-decrypt",
        "-binary",
        "-inform",
        "DER",
        "-in",
        str(ciphertext),
        "-recip",
        str(cert),
        "-inkey",
        str(key),
        "-out",
        str(restored),
    ]
    signing.run(command)
    with tarfile.open(restored) as bundle:
        assert set(bundle.getnames()) == set(files)
        for member in bundle:
            stream = bundle.extractfile(member)
            assert stream is not None and stream.read() == files[member.name]
    damaged = bytearray(ciphertext.read_bytes())
    damaged[-1] ^= 1
    ciphertext.write_bytes(damaged)
    with pytest.raises(signing.Denied):
        signing.run(command)
    # A failed GCM decrypt may leave unauthenticated partial output: never use it.


@pytest.mark.parametrize("case", ["mismatch", "missing", "private_key", "multiple", "signer"])
def test_bad_recipient_denied(tmp_path, recipient, case):
    key, _, original = recipient
    env = dict(original)
    if case == "mismatch":
        env["RECIPIENT_CERT_SHA256"] = "0" * 64
    elif case == "missing":
        env["RECIPIENT_CERT_PEM"] = ""
    elif case == "private_key":
        env["RECIPIENT_CERT_PEM"] = key.read_text()
    elif case == "multiple":
        env["RECIPIENT_CERT_PEM"] *= 2
    else:
        env["RECIPIENT_CERT_SHA256"] = signing.CERT
    with pytest.raises(signing.Denied):
        signing.recipient_certificate(tmp_path, env)


def test_bad_recipient_never_reaches_signer(tmp_path, monkeypatch):
    monkeypatch.setattr(signing, "gate", lambda *_, **__: {})
    signer = Mock(side_effect=AssertionError("must not sign"))
    monkeypatch.setattr(signing, "sign", signer)
    with pytest.raises(signing.Denied):
        signing.private_sign(tmp_path, {"RUNNER_TEMP": str(tmp_path)})
    signer.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_malformed_pem_whitespace_fails_before_sdk(tmp_path, monkeypatch):
    sdk = Mock(side_effect=AssertionError("must not invoke SDK"))
    monkeypatch.setattr(signing, "run", sdk)
    with pytest.raises(signing.Denied):
        signing.recipient_certificate(
            tmp_path,
            {
                "RECIPIENT_CERT_PEM": "-----BEGIN CERTIFICATE-----\n"
                + " " * 15000
                + "\n-----END CERTIFICATE-----",
                "RECIPIENT_CERT_SHA256": "0" * 64,
            },
        )
    sdk.assert_not_called()


@pytest.mark.parametrize("kind", ["weak_rsa", "ec", "near_expiry"])
def test_recipient_algorithm_strength_and_expiry(tmp_path, kind):
    key, cert = tmp_path / "test.key", tmp_path / "test.pem"
    algorithm = ["rsa:2048"] if kind == "weak_rsa" else ["rsa:3072"]
    if kind == "ec":
        algorithm = ["ec", "-pkeyopt", "ec_paramgen_curve:prime256v1"]
    signing.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            *algorithm,
            "-nodes",
            "-days",
            "1" if kind == "near_expiry" else "2",
            "-subj",
            "/CN=Disposable fixture",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ]
    )
    pem = cert.read_text()
    env = {
        "RECIPIENT_CERT_PEM": pem,
        "RECIPIENT_CERT_SHA256": hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest(),
    }
    with pytest.raises(signing.Denied):
        signing.recipient_certificate(tmp_path, env)


def test_wrong_decryption_key_cannot_recover_bundle(tmp_path, recipient):
    _, cert, _ = recipient
    signed = tmp_path / "signed"
    signed.mkdir()
    for name in (*signing.APKS, "artifact-only-manifest.json"):
        (signed / name).write_bytes(b"fixture-not-signed")
    sealed = tmp_path / "sealed"
    signing.seal_signed_bundle(signed, sealed, cert, {})
    wrong = tmp_path / "wrong.key"
    signing.run(
        [
            "openssl",
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            "rsa_keygen_bits:3072",
            "-out",
            str(wrong),
        ]
    )
    with pytest.raises(signing.Denied):
        signing.run(
            [
                "openssl",
                "cms",
                "-decrypt",
                "-binary",
                "-inform",
                "DER",
                "-in",
                str(sealed / "phone-wear.cms"),
                "-recip",
                str(cert),
                "-inkey",
                str(wrong),
                "-out",
                str(tmp_path / "invalid.tar"),
            ]
        )


@pytest.mark.parametrize("fail_encryption", [False, True])
def test_plaintext_is_temporary_and_never_uploadable(
    tmp_path, monkeypatch, recipient, fail_encryption
):
    _, _, env = recipient
    env = {**env, "RUNNER_TEMP": str(tmp_path)}
    evidence = {"sourceSha": "a" * 40, "recipientCertSha256": env["RECIPIENT_CERT_SHA256"]}
    monkeypatch.setattr(signing, "gate", lambda *_, **__: evidence)

    def fake_sign(root, incoming, outgoing, given):
        assert outgoing.parent.stat().st_mode & 0o777 == 0o700
        assert "private-artifacts" not in str(outgoing)
        outgoing.mkdir()
        for name in (*signing.APKS, "artifact-only-manifest.json"):
            (outgoing / name).write_bytes(b"fixture-not-signed")

    monkeypatch.setattr(signing, "sign", fake_sign)
    if fail_encryption:
        monkeypatch.setattr(signing, "seal_signed_bundle", Mock(side_effect=signing.Denied()))
        with pytest.raises(signing.Denied):
            signing.private_sign(tmp_path, env)
        assert list(tmp_path.iterdir()) == []
    else:
        signing.private_sign(tmp_path, env)
        assert {p.name for p in tmp_path.iterdir()} == {"private-artifacts"}
        assert not list(tmp_path.rglob("*.apk"))


def test_only_fixed_get_requests_and_no_publication_code():
    root = Path(__file__).resolve().parents[2]
    source = (root / "tools/artifact_signing.py").read_text()
    assert 'method="GET"' in source
    for forbidden in ("gh release", "git push", "adb ", "systemctl", "requests.post", "shell=True"):
        assert forbidden not in source


@pytest.mark.parametrize("version,code", [("19.0.0-alpha43", 190450), ("19.0.0-alpha44", 190460)])
def test_canonical_candidate_and_future_recovery_identity(tmp_path, monkeypatch, version, code):
    # alpha44 is a policy fixture, not a release reservation or approval.
    (tmp_path / "docs").mkdir()
    signing.write_json(
        tmp_path / "docs/JARVIS_PRODUCT_BASELINE.json",
        {
            "current_release": {
                "version_name": version,
                "version_code": code,
                "phone_package": signing.PACKAGE,
                "production_signer_sha256": signing.CERT,
            }
        },
    )
    monkeypatch.setattr(signing, "run", Mock(side_effect=["a" * 40, ""]))
    assert signing.source_identity(tmp_path, "a" * 40) == (version, code)
