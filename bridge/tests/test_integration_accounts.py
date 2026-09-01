from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.integration_accounts import (
    CredentialCipher,
    IntegrationAccountStore,
    OAuthSessionError,
)


def _key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


def test_credential_cipher_is_authenticated_purpose_bound_and_redacted() -> None:
    cipher = CredentialCipher(_key())
    encrypted = cipher.encrypt(
        {"access_token": "access-secret", "refresh_token": "refresh-secret"},
        purpose="account:a",
    )

    assert "access-secret" not in encrypted
    assert "refresh-secret" not in encrypted
    assert cipher.decrypt(encrypted, purpose="account:a")["refresh_token"] == "refresh-secret"
    with pytest.raises(ValueError, match="authenticated"):
        cipher.decrypt(encrypted, purpose="account:b")
    with pytest.raises(ValueError, match="authenticated"):
        cipher.decrypt(encrypted[:-2] + "AA", purpose="account:a")
    with pytest.raises(ValueError, match="URL-safe base64"):
        CredentialCipher("!" * 44)


@pytest.mark.asyncio
async def test_oauth_state_is_one_time_expiring_encrypted_and_principal_scoped(
    tmp_path: Path,
) -> None:
    store = IntegrationAccountStore(tmp_path / "accounts.db", CredentialCipher(_key()))
    await store.initialize()
    state = secrets.token_urlsafe(48)
    created = await store.create_oauth_session(
        provider="google",
        principal_id="aaron",
        redirect_uri="https://core.example/api/integrations/google/callback",
        requested_scopes=("openid", "email"),
        state=state,
        code_verifier="verifier-secret",
    )

    assert stat_mode(store.path) == 0o600
    connection = store._db()
    try:
        for suffix in ("-wal", "-shm"):
            auxiliary = Path(str(store.path) + suffix)
            if auxiliary.exists():
                assert stat_mode(auxiliary) == 0o600
    finally:
        connection.close()
    assert "verifier-secret" not in store.path.read_bytes().decode("latin-1")
    first, second = await asyncio.gather(
        store.claim_oauth_callback(provider="google", state=state),
        store.claim_oauth_callback(provider="google", state=state),
        return_exceptions=True,
    )
    outcomes = (first, second)
    assert sum(not isinstance(item, BaseException) for item in outcomes) == 1
    assert sum(isinstance(item, OAuthSessionError) for item in outcomes) == 1
    claimed = next(item for item in outcomes if not isinstance(item, BaseException))
    assert claimed.session_id == created.session_id
    assert claimed.code_verifier == "verifier-secret"
    assert await store.oauth_session(created.session_id, principal_id="amber") is None
    assert await store.oauth_session(created.session_id, principal_id="aaron") is not None

    with pytest.raises(OAuthSessionError, match="malformed"):
        await store.claim_oauth_callback(provider="google", state="x" * 20_000)

    expired_state = secrets.token_urlsafe(48)
    expired = await store.create_oauth_session(
        provider="google",
        principal_id="aaron",
        redirect_uri="https://core.example/api/integrations/google/callback",
        requested_scopes=("openid", "email"),
        state=expired_state,
        code_verifier="expired-verifier",
    )
    with store._db() as connection:
        connection.execute(
            "UPDATE integration_oauth_sessions SET expires_at=? WHERE session_id=?",
            (
                (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                expired.session_id,
            ),
        )
    with pytest.raises(OAuthSessionError, match="expired"):
        await store.claim_oauth_callback(provider="google", state=expired_state)


@pytest.mark.asyncio
async def test_accounts_are_isolated_and_credentials_are_never_in_status(
    tmp_path: Path,
) -> None:
    store = IntegrationAccountStore(tmp_path / "accounts.db", CredentialCipher(_key()))
    await store.initialize()
    account_id = await store.upsert_account(
        provider="google",
        principal_id="aaron",
        provider_subject="subject-1",
        display_name="Aaron",
        email="aaron@example.test",
        scopes=("scope.read",),
        credentials={"access_token": "access-secret", "refresh_token": "refresh-secret"},
    )

    assert (
        await store.account(principal_id="amber", provider="google", account_id=account_id) is None
    )
    row = await store.account(principal_id="aaron", provider="google", account_id=account_id)
    assert row is not None
    assert bool(row["authenticated"]) is True
    assert bool(row["healthy"]) is False
    assert row["last_health_check"] is None
    assert row["health_reason"] == "Provider health has not yet been verified"
    assert "access-secret" not in repr(dict(row))
    credential_status = await store.credential_status(account_id)
    assert credential_status["access_token_present"] is True
    assert credential_status["refresh_token_present"] is True
    assert "access-secret" not in json.dumps(credential_status)
    assert "refresh-secret" not in json.dumps(credential_status)
    assert await store.delete_account(principal_id="amber", account_id=account_id) is False
    assert await store.delete_account(principal_id="aaron", account_id=account_id) is True


@pytest.mark.asyncio
async def test_account_and_oauth_state_survive_store_restart(tmp_path: Path) -> None:
    key = _key()
    path = tmp_path / "accounts.db"
    first = IntegrationAccountStore(path, CredentialCipher(key))
    await first.initialize()
    account_id = await first.upsert_account(
        provider="google",
        principal_id="aaron",
        provider_subject="subject-1",
        display_name="Aaron",
        email="aaron@example.test",
        scopes=("scope.read",),
        credentials={"access_token": "access-secret", "refresh_token": "refresh-secret"},
    )
    session = await first.create_oauth_session(
        provider="google",
        principal_id="aaron",
        redirect_uri="https://core.example/api/integrations/google/callback",
        requested_scopes=("openid", "email"),
        state=secrets.token_urlsafe(48),
        code_verifier="restart-verifier",
    )

    restarted = IntegrationAccountStore(path, CredentialCipher(key))
    await restarted.initialize()
    account = await restarted.account(
        principal_id="aaron",
        provider="google",
        account_id=account_id,
    )
    restored_session = await restarted.oauth_session(session.session_id, principal_id="aaron")

    assert account is not None and account["email"] == "aaron@example.test"
    assert (await restarted.account_credentials(account_id))["refresh_token"] == "refresh-secret"
    assert restored_session is not None and restored_session.status == "pending"
    assert (await restarted.database_health())["healthy"] is True


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777
