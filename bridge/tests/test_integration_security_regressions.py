from __future__ import annotations

import time

from app.connectors.credentials import REDACTED, redact_request_target, redact_text
from app.followup_schedule import parse_periodic_followup


def test_private_key_redaction_is_linear_and_fails_closed_for_truncated_keys() -> None:
    adversarial = "-----BEGIN PRIVATE KEY-----" * 40_000  # gitleaks:allow
    started = time.monotonic()
    redacted = redact_text(adversarial, max_length=20_000)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert "BEGIN PRIVATE KEY" not in redacted
    assert REDACTED in redacted

    complete = (
        "prefix -----BEGIN RSA PRIVATE KEY-----\nvery-secret\n-----END RSA PRIVATE KEY----- suffix"
    )
    assert redact_text(complete) == f"prefix {REDACTED} suffix"


def test_oauth_callback_request_target_never_logs_code_or_state() -> None:
    target = (
        "/api/integrations/google/callback?code=oauth-code-secret&"
        "state=csrf-secret&scope=openid%20email"
    )

    redacted = redact_request_target(target)

    assert "oauth-code-secret" not in redacted
    assert "csrf-secret" not in redacted
    assert f"code={REDACTED}" in redacted
    assert f"state={REDACTED}" in redacted
    assert "scope=openid%20email" in redacted


def test_periodic_monitor_parser_is_bounded_on_adversarial_user_input() -> None:
    adversarial = "monitor _.0" + "00" * 500_000
    started = time.monotonic()
    result = parse_periodic_followup(adversarial)
    elapsed = time.monotonic() - started

    assert result is None
    assert elapsed < 0.2
    assert parse_periodic_followup("monitor sensor.garage every 15 minutes") == (
        "sensor.garage",
        900,
    )
    assert parse_periodic_followup("keep an eye on binary_sensor.door") == (
        "binary_sensor.door",
        3600,
    )
