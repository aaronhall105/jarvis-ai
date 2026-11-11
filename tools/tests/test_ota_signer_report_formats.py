"""Strict signing-evidence tests; never publish or read signing secrets."""

import pytest

from tools.publish_android_ota import CERT, PublicationError, verify_signer_report


def report(*lines: str, signer_count: str = "1") -> bytes:
    return "\n".join(
        ("Verifies", f"Number of signers: {signer_count}", *lines, "")
    ).encode()


@pytest.mark.parametrize(
    "label",
    ["Signer #1", "V2 Signer:", "V3.0 Signer:", "V3.1 Signer:"],
)
def test_supported_signing_report_styles(label: str) -> None:
    verify_signer_report(report(f"{label} certificate SHA-256 digest: {CERT}"))


def test_two_schemes_same_one_signer_are_valid() -> None:
    verify_signer_report(
        report(
            f"V2 Signer: certificate SHA-256 digest: {CERT}",
            f"V3.0 Signer: certificate SHA-256 digest: {CERT}",
        )
    )


@pytest.mark.parametrize(
    "bad",
    [
        report(f"Signer #1 certificate SHA-256 digest: {'0' * 64}"),
        report(f"Signer #2 certificate SHA-256 digest: {CERT}"),
        report(f"Signer #1 certificate SHA-256 digest: {CERT}", signer_count="2"),
        report(f"Signer #1 certificate SHA-256 digest: {CERT}").replace(
            b"Verifies", b"DOES NOT VERIFY"
        ),
        report(f"Signer #1 certificate SHA-256 digest: {CERT}").replace(
            b"Number of signers: 1\n", b""
        ),
        report(
            f"V3.0 Signer: certificate SHA-256 digest: {CERT}",
            f"V2 Signer: certificate SHA-256 digest: {'1' * 64}",
        ),
        report(f"Unknown Signer: certificate SHA-256 digest: {CERT}"),
        report(f"Signer #1 certificate SHA-256 digest: {CERT[:-1]}?"),
        b"\xff",
    ],
)
def test_rejects_invalid_signer_evidence(bad: bytes) -> None:
    with pytest.raises(PublicationError, match="signer_report_mismatch"):
        verify_signer_report(bad)
