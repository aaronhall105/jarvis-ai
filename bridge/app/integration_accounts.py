"""Durable, encrypted integration accounts and one-time OAuth sessions."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.connectors.credentials import redact_text


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utc_now()).astimezone(timezone.utc).isoformat()


class CredentialEncryptionUnavailable(RuntimeError):
    """Raised when credential persistence has no valid host-supplied key."""


class OAuthSessionError(ValueError):
    """Raised for expired, replayed, malformed, or mismatched OAuth state."""


class CredentialCipher:
    """AES-256-GCM envelope encryption with caller-bound associated data."""

    def __init__(self, encoded_key: str) -> None:
        self._key: bytes | None = None
        value = str(encoded_key or "").strip()
        if not value:
            return
        try:
            key = base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("Credential encryption key must be URL-safe base64") from exc
        if len(key) != 32:
            raise ValueError("Credential encryption key must decode to exactly 32 bytes")
        self._key = key

    @property
    def configured(self) -> bool:
        return self._key is not None

    def encrypt(self, value: Mapping[str, Any], *, purpose: str) -> str:
        if self._key is None:
            raise CredentialEncryptionUnavailable(
                "Credential encryption is not configured on the Core host"
            )
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        nonce = secrets.token_bytes(12)
        encrypted = AESGCM(self._key).encrypt(nonce, encoded, purpose.encode("utf-8"))
        return "v1." + base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")

    def decrypt(self, value: str, *, purpose: str) -> dict[str, Any]:
        if self._key is None:
            raise CredentialEncryptionUnavailable(
                "Credential encryption is not configured on the Core host"
            )
        prefix, separator, payload = str(value or "").partition(".")
        if prefix != "v1" or separator != ".":
            raise ValueError("Unsupported encrypted credential envelope")
        try:
            raw = base64.urlsafe_b64decode(payload.encode("ascii"))
            decoded = AESGCM(self._key).decrypt(raw[:12], raw[12:], purpose.encode("utf-8"))
            result = json.loads(decoded)
        except Exception as exc:
            raise ValueError("Encrypted credentials could not be authenticated") from exc
        if not isinstance(result, dict):
            raise ValueError("Encrypted credential payload is malformed")
        return result


@dataclass(frozen=True, slots=True)
class IntegrationAccount:
    provider: str
    account_id: str
    principal_id: str
    provider_subject: str
    account_display_name: str
    account_email: str | None
    configured: bool
    authenticated: bool
    healthy: bool
    granted_scopes: tuple[str, ...]
    available_capabilities: tuple[str, ...]
    read_capabilities: tuple[str, ...]
    write_capabilities: tuple[str, ...]
    last_health_check: str | None
    reauthorization_required: bool
    setup_requirements: tuple[str, ...]
    health_reason: str | None
    created_at: str
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        """Return the complete redacted account contract; never credentials."""

        return {
            "provider": self.provider,
            "account_id": self.account_id,
            "account_display_name": self.account_display_name,
            "account_email": self.account_email,
            "configured": self.configured,
            "authenticated": self.authenticated,
            "healthy": self.healthy,
            "granted_scopes": list(self.granted_scopes),
            "available_capabilities": list(self.available_capabilities),
            "read_capabilities": list(self.read_capabilities),
            "write_capabilities": list(self.write_capabilities),
            "last_health_check": self.last_health_check,
            "reauthorization_required": self.reauthorization_required,
            "setup_requirements": list(self.setup_requirements),
            "health_reason": self.health_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class OAuthSession:
    session_id: str
    provider: str
    principal_id: str
    redirect_uri: str
    requested_scopes: tuple[str, ...]
    status: str
    error: str | None
    account_id: str | None
    created_at: str
    expires_at: str
    consumed_at: str | None
    code_verifier: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "provider": self.provider,
            "status": self.status,
            "error": self.error,
            "account_id": self.account_id,
            "requested_scopes": list(self.requested_scopes),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "completed": self.status == "connected",
        }


class IntegrationAccountStore:
    """SQLite metadata plus authenticated encryption for credential material."""

    def __init__(self, path: str | Path, cipher: CredentialCipher) -> None:
        self.path = Path(path)
        self.cipher = cipher

    def _db(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            for candidate in (
                self.path,
                Path(str(self.path) + "-wal"),
                Path(str(self.path) + "-shm"),
            ):
                if candidate.exists():
                    os.chmod(candidate, 0o600)
        except OSError:
            connection.close()
            raise
        return connection

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with self._db() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS integration_accounts (
                    account_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    provider_subject TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    email TEXT,
                    scopes_json TEXT NOT NULL,
                    encrypted_credentials TEXT NOT NULL,
                    authenticated INTEGER NOT NULL,
                    healthy INTEGER NOT NULL,
                    health_reason TEXT,
                    reauthorization_required INTEGER NOT NULL,
                    last_health_check TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(provider, principal_id, provider_subject)
                );
                CREATE INDEX IF NOT EXISTS idx_integration_accounts_owner
                    ON integration_accounts(principal_id, provider);
                CREATE TABLE IF NOT EXISTS integration_oauth_sessions (
                    session_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    state_hash TEXT NOT NULL UNIQUE,
                    encrypted_verifier TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    account_id TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_oauth_sessions_owner
                    ON integration_oauth_sessions(principal_id, provider, created_at);
                """
            )

    @staticmethod
    def _account_purpose(account_id: str) -> str:
        return f"jarvis:integration-account:{account_id}"

    @staticmethod
    def _oauth_purpose(session_id: str) -> str:
        return f"jarvis:oauth-session:{session_id}"

    @staticmethod
    def _state_hash(state: str) -> str:
        return hashlib.sha256(str(state).encode("utf-8")).hexdigest()

    async def create_oauth_session(
        self,
        *,
        provider: str,
        principal_id: str,
        redirect_uri: str,
        requested_scopes: Sequence[str],
        state: str,
        code_verifier: str,
        ttl_seconds: int = 600,
    ) -> OAuthSession:
        return await asyncio.to_thread(
            self._create_oauth_session_sync,
            provider,
            principal_id,
            redirect_uri,
            tuple(requested_scopes),
            state,
            code_verifier,
            ttl_seconds,
        )

    def _create_oauth_session_sync(
        self,
        provider: str,
        principal_id: str,
        redirect_uri: str,
        requested_scopes: tuple[str, ...],
        state: str,
        code_verifier: str,
        ttl_seconds: int,
    ) -> OAuthSession:
        session_id = str(uuid.uuid4())
        created = _utc_now()
        expires = created + timedelta(seconds=max(60, min(int(ttl_seconds), 1800)))
        verifier = self.cipher.encrypt(
            {"code_verifier": code_verifier},
            purpose=self._oauth_purpose(session_id),
        )
        scopes = tuple(dict.fromkeys(str(item).strip() for item in requested_scopes if item))
        with self._db() as connection:
            connection.execute(
                "DELETE FROM integration_oauth_sessions WHERE expires_at<?",
                (_iso(created - timedelta(days=1)),),
            )
            connection.execute(
                """
                INSERT INTO integration_oauth_sessions(
                    session_id,provider,principal_id,state_hash,encrypted_verifier,
                    redirect_uri,scopes_json,status,error,account_id,created_at,
                    expires_at,consumed_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    session_id,
                    provider,
                    principal_id,
                    self._state_hash(state),
                    verifier,
                    redirect_uri,
                    json.dumps(scopes, separators=(",", ":")),
                    "pending",
                    None,
                    None,
                    _iso(created),
                    _iso(expires),
                    None,
                ),
            )
        return OAuthSession(
            session_id=session_id,
            provider=provider,
            principal_id=principal_id,
            redirect_uri=redirect_uri,
            requested_scopes=scopes,
            status="pending",
            error=None,
            account_id=None,
            created_at=_iso(created),
            expires_at=_iso(expires),
            consumed_at=None,
        )

    async def claim_oauth_callback(self, *, provider: str, state: str) -> OAuthSession:
        return await asyncio.to_thread(self._claim_oauth_callback_sync, provider, state)

    def _claim_oauth_callback_sync(self, provider: str, state: str) -> OAuthSession:
        if not state or len(state) < 32 or len(state) > 512 or not state.isascii():
            raise OAuthSessionError("OAuth state is missing or malformed")
        now = _utc_now()
        with self._db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM integration_oauth_sessions WHERE state_hash=?",
                (self._state_hash(state),),
            ).fetchone()
            if row is None or row["provider"] != provider:
                raise OAuthSessionError("OAuth state is invalid")
            if row["status"] != "pending" or row["consumed_at"] is not None:
                raise OAuthSessionError("OAuth callback has already been used")
            if datetime.fromisoformat(row["expires_at"]) <= now:
                connection.execute(
                    "UPDATE integration_oauth_sessions SET status='expired',error=? WHERE session_id=?",
                    ("OAuth connection timed out", row["session_id"]),
                )
                raise OAuthSessionError("OAuth state has expired")
            consumed = _iso(now)
            connection.execute(
                """
                UPDATE integration_oauth_sessions
                SET status='exchanging',consumed_at=?
                WHERE session_id=? AND status='pending' AND consumed_at IS NULL
                """,
                (consumed, row["session_id"]),
            )
            if connection.total_changes != 1:
                raise OAuthSessionError("OAuth callback has already been used")
        verifier = self.cipher.decrypt(
            row["encrypted_verifier"],
            purpose=self._oauth_purpose(row["session_id"]),
        ).get("code_verifier")
        if not isinstance(verifier, str) or not verifier:
            raise OAuthSessionError("OAuth PKCE verifier is unavailable")
        return self._oauth_from_row(
            row, status="exchanging", consumed_at=consumed, verifier=verifier
        )

    async def oauth_session(self, session_id: str, *, principal_id: str) -> OAuthSession | None:
        return await asyncio.to_thread(self._oauth_session_sync, session_id, principal_id)

    def _oauth_session_sync(self, session_id: str, principal_id: str) -> OAuthSession | None:
        with self._db() as connection:
            row = connection.execute(
                """
                SELECT * FROM integration_oauth_sessions
                WHERE session_id=? AND principal_id=?
                """,
                (session_id, principal_id),
            ).fetchone()
        return self._oauth_from_row(row) if row is not None else None

    async def complete_oauth_session(self, session_id: str, account_id: str) -> None:
        await asyncio.to_thread(
            self._finish_oauth_session_sync, session_id, "connected", None, account_id
        )

    async def fail_oauth_session(self, session_id: str, error: str) -> None:
        await asyncio.to_thread(
            self._finish_oauth_session_sync,
            session_id,
            "failed",
            redact_text(error, max_length=500),
            None,
        )

    def _finish_oauth_session_sync(
        self, session_id: str, status: str, error: str | None, account_id: str | None
    ) -> None:
        with self._db() as connection:
            cursor = connection.execute(
                """
                UPDATE integration_oauth_sessions
                SET status=?,error=?,account_id=?,encrypted_verifier='consumed'
                WHERE session_id=? AND status='exchanging'
                """,
                (status, error, account_id, session_id),
            )
            if cursor.rowcount != 1:
                raise OAuthSessionError("OAuth session is not awaiting completion")

    @staticmethod
    def _oauth_from_row(
        row: sqlite3.Row,
        *,
        status: str | None = None,
        consumed_at: str | None = None,
        verifier: str | None = None,
    ) -> OAuthSession:
        return OAuthSession(
            session_id=row["session_id"],
            provider=row["provider"],
            principal_id=row["principal_id"],
            redirect_uri=row["redirect_uri"],
            requested_scopes=tuple(json.loads(row["scopes_json"])),
            status=status or row["status"],
            error=row["error"],
            account_id=row["account_id"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            consumed_at=consumed_at if consumed_at is not None else row["consumed_at"],
            code_verifier=verifier,
        )

    async def upsert_account(
        self,
        *,
        provider: str,
        principal_id: str,
        provider_subject: str,
        display_name: str,
        email: str | None,
        scopes: Sequence[str],
        credentials: Mapping[str, Any],
    ) -> str:
        return await asyncio.to_thread(
            self._upsert_account_sync,
            provider,
            principal_id,
            provider_subject,
            display_name,
            email,
            tuple(scopes),
            dict(credentials),
        )

    def _upsert_account_sync(
        self,
        provider: str,
        principal_id: str,
        provider_subject: str,
        display_name: str,
        email: str | None,
        scopes: tuple[str, ...],
        credentials: dict[str, Any],
    ) -> str:
        now = _iso()
        with self._db() as connection:
            existing = connection.execute(
                """
                SELECT account_id FROM integration_accounts
                WHERE provider=? AND principal_id=? AND provider_subject=?
                """,
                (provider, principal_id, provider_subject),
            ).fetchone()
            account_id = existing["account_id"] if existing else str(uuid.uuid4())
            encrypted = self.cipher.encrypt(
                credentials,
                purpose=self._account_purpose(account_id),
            )
            values = (
                account_id,
                provider,
                principal_id,
                provider_subject,
                str(display_name or email or provider).strip()[:300],
                str(email).strip()[:320] if email else None,
                json.dumps(sorted(set(scopes)), separators=(",", ":")),
                encrypted,
                1,
                1,
                None,
                0,
                now,
                now,
                now,
            )
            connection.execute(
                """
                INSERT INTO integration_accounts(
                    account_id,provider,principal_id,provider_subject,display_name,
                    email,scopes_json,encrypted_credentials,authenticated,healthy,
                    health_reason,reauthorization_required,last_health_check,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(provider,principal_id,provider_subject) DO UPDATE SET
                    display_name=excluded.display_name,
                    email=excluded.email,
                    scopes_json=excluded.scopes_json,
                    encrypted_credentials=excluded.encrypted_credentials,
                    authenticated=1,
                    healthy=1,
                    health_reason=NULL,
                    reauthorization_required=0,
                    last_health_check=excluded.last_health_check,
                    updated_at=excluded.updated_at
                """,
                values,
            )
        return account_id

    async def account(
        self, *, principal_id: str, provider: str, account_id: str | None = None
    ) -> sqlite3.Row | None:
        return await asyncio.to_thread(self._account_sync, principal_id, provider, account_id)

    def _account_sync(
        self, principal_id: str, provider: str, account_id: str | None
    ) -> sqlite3.Row | None:
        with self._db() as connection:
            if account_id:
                return connection.execute(
                    """
                    SELECT * FROM integration_accounts
                    WHERE principal_id=? AND provider=? AND account_id=?
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (principal_id, provider, account_id),
                ).fetchone()
            return connection.execute(
                """
                SELECT * FROM integration_accounts
                WHERE principal_id=? AND provider=?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (principal_id, provider),
            ).fetchone()

    async def account_credentials(self, account_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._account_credentials_sync, account_id)

    def _account_credentials_sync(self, account_id: str) -> dict[str, Any]:
        with self._db() as connection:
            row = connection.execute(
                "SELECT encrypted_credentials FROM integration_accounts WHERE account_id=?",
                (account_id,),
            ).fetchone()
        if row is None:
            raise KeyError(account_id)
        return self.cipher.decrypt(
            row["encrypted_credentials"],
            purpose=self._account_purpose(account_id),
        )

    async def update_credentials(
        self,
        account_id: str,
        credentials: Mapping[str, Any],
        *,
        scopes: Sequence[str] | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._update_credentials_sync,
            account_id,
            dict(credentials),
            tuple(scopes) if scopes is not None else None,
        )

    def _update_credentials_sync(
        self,
        account_id: str,
        credentials: dict[str, Any],
        scopes: tuple[str, ...] | None,
    ) -> None:
        encrypted = self.cipher.encrypt(
            credentials,
            purpose=self._account_purpose(account_id),
        )
        now = _iso()
        with self._db() as connection:
            if scopes is not None:
                connection.execute(
                    """
                    UPDATE integration_accounts
                    SET encrypted_credentials=?,authenticated=1,healthy=1,
                        reauthorization_required=0,health_reason=NULL,
                        last_health_check=?,updated_at=?,scopes_json=?
                    WHERE account_id=?
                    """,
                    (
                        encrypted,
                        now,
                        now,
                        json.dumps(sorted(set(scopes)), separators=(",", ":")),
                        account_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE integration_accounts
                    SET encrypted_credentials=?,authenticated=1,healthy=1,
                        reauthorization_required=0,health_reason=NULL,
                        last_health_check=?,updated_at=?
                    WHERE account_id=?
                    """,
                    (encrypted, now, now, account_id),
                )

    async def mark_health(
        self,
        account_id: str,
        *,
        authenticated: bool,
        healthy: bool,
        reason: str | None,
        reauthorization_required: bool = False,
    ) -> None:
        await asyncio.to_thread(
            self._mark_health_sync,
            account_id,
            authenticated,
            healthy,
            redact_text(reason, max_length=500) if reason else None,
            reauthorization_required,
        )

    def _mark_health_sync(
        self,
        account_id: str,
        authenticated: bool,
        healthy: bool,
        reason: str | None,
        reauthorization_required: bool,
    ) -> None:
        now = _iso()
        with self._db() as connection:
            connection.execute(
                """
                UPDATE integration_accounts
                SET authenticated=?,healthy=?,health_reason=?,
                    reauthorization_required=?,last_health_check=?,updated_at=?
                WHERE account_id=?
                """,
                (
                    int(authenticated),
                    int(healthy),
                    reason,
                    int(reauthorization_required),
                    now,
                    now,
                    account_id,
                ),
            )

    async def delete_account(self, *, principal_id: str, account_id: str) -> bool:
        return await asyncio.to_thread(self._delete_account_sync, principal_id, account_id)

    def _delete_account_sync(self, principal_id: str, account_id: str) -> bool:
        with self._db() as connection:
            cursor = connection.execute(
                "DELETE FROM integration_accounts WHERE principal_id=? AND account_id=?",
                (principal_id, account_id),
            )
            return cursor.rowcount == 1

    async def database_health(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._database_health_sync)

    def _database_health_sync(self) -> dict[str, Any]:
        try:
            with self._db() as connection:
                quick = connection.execute("PRAGMA quick_check(1)").fetchone()
                schema = connection.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type='table' AND name LIKE 'integration_%'"
                ).fetchone()
            healthy = bool(quick and quick[0] == "ok" and schema and schema[0] >= 2)
        except sqlite3.Error:
            healthy = False
        return {"healthy": healthy, "reason": None if healthy else "Account database probe failed"}


__all__ = [
    "CredentialCipher",
    "CredentialEncryptionUnavailable",
    "IntegrationAccount",
    "IntegrationAccountStore",
    "OAuthSession",
    "OAuthSessionError",
]
