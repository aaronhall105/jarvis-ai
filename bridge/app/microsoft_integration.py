"""Microsoft identity platform OAuth and Microsoft Graph mail connector.

The connector deliberately exposes reversible mailbox mutations only.  There
is no permanent-delete capability and no capability for controlling or
"forcing" an Outlook client application to sync.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses
from typing import Any, Mapping, Sequence
from urllib.parse import quote, urlencode, urlsplit

import httpx

from app.connectors import (
    CapabilityAccess,
    CapabilityMetadata,
    CapabilityRequest,
    ConfirmationMode,
    Connector,
    ConnectorResult,
    ProviderStatus,
    RiskLevel,
    VerificationMode,
    VerificationResult,
)
from app.connectors.credentials import redact_text
from app.integration_accounts import (
    CredentialCipher,
    CredentialEncryptionUnavailable,
    IntegrationAccount,
    IntegrationAccountStore,
    OAuthSession,
    OAuthSessionError,
)


GRAPH_API = "https://graph.microsoft.com/v1.0"
MICROSOFT_PROVIDER = "microsoft"
MICROSOFT_MODEL_TOOL = "microsoft_email_integration"
SCOPE_OPENID = "openid"
SCOPE_PROFILE = "profile"
SCOPE_EMAIL = "email"
SCOPE_OFFLINE = "offline_access"
SCOPE_USER_READ = "User.Read"
SCOPE_MAIL_READ_WRITE = "Mail.ReadWrite"
SCOPE_MAIL_SEND = "Mail.Send"
DEFAULT_MICROSOFT_SCOPES = (
    SCOPE_OPENID,
    SCOPE_PROFILE,
    SCOPE_EMAIL,
    SCOPE_OFFLINE,
    SCOPE_USER_READ,
    SCOPE_MAIL_READ_WRITE,
    SCOPE_MAIL_SEND,
)

_ARGUMENT_GUIDANCE: Mapping[str, str] = {
    "outlook.account": "no arguments",
    "outlook.folders": "no arguments",
    "outlook.search": "query and optional limit/folder",
    "outlook.changes": "optional delta_link and limit",
    "outlook.read": "message_id",
    "outlook.thread": "conversation_id and optional limit",
    "outlook.draft": "to, subject and body",
    "outlook.reply": "message_id and body; creates a reply draft",
    "outlook.send": "draft_id",
    "outlook.archive": "message_id",
    "outlook.trash": "message_id",
    "outlook.restore": "message_id and exact destination_id",
    "outlook.mark_read": "message_id",
    "outlook.mark_unread": "message_id",
}


def microsoft_model_tool(executable_capabilities: Sequence[str]) -> dict[str, Any] | None:
    capabilities = tuple(
        item
        for item in dict.fromkeys(str(value) for value in executable_capabilities)
        if item in _ARGUMENT_GUIDANCE
    )
    if not capabilities:
        return None
    guidance = "; ".join(f"{item}: {_ARGUMENT_GUIDANCE[item]}" for item in capabilities)
    return {
        "type": "function",
        "name": MICROSOFT_MODEL_TOOL,
        "description": (
            "Use the authenticated Microsoft Graph mailbox. IDs, provider and account must "
            "come from current structured evidence. Never infer an Outlook message from a "
            "Gmail ID. Draft/reply only create drafts; outlook.send sends an exact draft. "
            "There is no permanent-delete or Outlook-app sync capability. Contracts: " + guidance
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "capability_id": {"type": "string", "enum": list(capabilities)},
                "arguments": {"type": "object"},
            },
            "required": ["capability_id", "arguments"],
            "additionalProperties": False,
        },
        "strict": False,
    }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utc_now()).astimezone(timezone.utc).isoformat()


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


class MicrosoftProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        outcome_unknown: bool = False,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.outcome_unknown = outcome_unknown
        self._retryable = retryable

    @property
    def reauthorization_required(self) -> bool:
        return self.status_code in {400, 401}

    @property
    def retryable(self) -> bool:
        if self._retryable is not None:
            return self._retryable
        return self.status_code == 429 or bool(self.status_code and self.status_code >= 500)


@dataclass(frozen=True, slots=True)
class MicrosoftOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    authority: str = "common"
    android_return_uri: str = "jarvis://integrations/microsoft"

    @property
    def tenant(self) -> str | None:
        value = self.authority.strip().casefold()
        if value in {"common", "organizations", "consumers"}:
            return value
        if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value):
            return value
        return None

    @property
    def configured(self) -> bool:
        if not self.client_id.strip() or not self.client_secret.strip() or self.tenant is None:
            return False
        try:
            redirect = urlsplit(self.redirect_uri.strip())
            android = urlsplit(self.android_return_uri.strip())
        except ValueError:
            return False
        secure = redirect.scheme == "https" or (
            redirect.scheme == "http" and redirect.hostname in {"localhost", "127.0.0.1"}
        )
        return bool(
            secure
            and redirect.hostname
            and not redirect.username
            and not redirect.password
            and not redirect.query
            and not redirect.fragment
            and android.scheme == "jarvis"
            and android.hostname == "integrations"
            and android.path == "/microsoft"
            and not android.query
            and not android.fragment
        )

    @property
    def authorization_endpoint(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant or 'common'}/oauth2/v2.0/authorize"

    @property
    def token_endpoint(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant or 'common'}/oauth2/v2.0/token"

    @property
    def setup_requirements(self) -> tuple[str, ...]:
        missing: list[str] = []
        if not self.client_id.strip():
            missing.append("Configure JARVIS_MICROSOFT_CLIENT_ID")
        if not self.client_secret.strip():
            missing.append("Configure JARVIS_MICROSOFT_CLIENT_SECRET")
        if not self.redirect_uri.strip():
            missing.append("Configure JARVIS_MICROSOFT_REDIRECT_URI")
        if self.tenant is None:
            missing.append("Use common, organizations, consumers, or an exact tenant ID")
        if self.redirect_uri.strip() and not self.configured:
            missing.append("Use an exact HTTPS or loopback Microsoft OAuth redirect URI")
        return tuple(dict.fromkeys(missing))


class MicrosoftOAuthService:
    """Server-side authorization-code flow protected by PKCE and one-time state."""

    def __init__(
        self,
        *,
        config: MicrosoftOAuthConfig,
        accounts: IntegrationAccountStore,
        cipher: CredentialCipher,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30,
    ) -> None:
        self.config = config
        self.accounts = accounts
        self.cipher = cipher
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(max(5.0, min(float(timeout_seconds), 60.0))),
            follow_redirects=False,
            trust_env=False,
        )

    @property
    def configured(self) -> bool:
        return self.config.configured and self.cipher.configured

    @property
    def setup_requirements(self) -> tuple[str, ...]:
        values = list(self.config.setup_requirements)
        if not self.cipher.configured:
            values.append("Configure JARVIS_CREDENTIAL_ENCRYPTION_KEY")
        return tuple(values)

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def start(self, *, principal_id: str) -> dict[str, Any]:
        if not self.configured:
            raise RuntimeError("Microsoft OAuth setup is incomplete")
        principal = str(principal_id or "").strip()
        if not principal:
            raise ValueError("An authenticated principal is required")
        state = secrets.token_urlsafe(48)
        verifier = secrets.token_urlsafe(64)
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        session = await self.accounts.create_oauth_session(
            provider=MICROSOFT_PROVIDER,
            principal_id=principal,
            redirect_uri=self.config.redirect_uri.strip(),
            requested_scopes=DEFAULT_MICROSOFT_SCOPES,
            state=state,
            code_verifier=verifier,
        )
        query = urlencode(
            {
                "client_id": self.config.client_id.strip(),
                "response_type": "code",
                "redirect_uri": self.config.redirect_uri.strip(),
                "response_mode": "query",
                "scope": " ".join(DEFAULT_MICROSOFT_SCOPES),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
        return {
            "session": session.as_dict(),
            "authorization_url": f"{self.config.authorization_endpoint}?{query}",
        }

    async def callback(
        self, *, state: str, code: str | None, provider_error: str | None = None
    ) -> OAuthSession:
        session = await self.accounts.claim_oauth_callback(provider=MICROSOFT_PROVIDER, state=state)
        if provider_error:
            message = "Microsoft authorization was cancelled or denied"
            await self.accounts.fail_oauth_session(session.session_id, message)
            raise OAuthSessionError(message)
        if not code or len(str(code)) > 4096:
            await self.accounts.fail_oauth_session(
                session.session_id, "Microsoft did not return an authorization code"
            )
            raise OAuthSessionError("Microsoft did not return an authorization code")
        try:
            token = await self._exchange_code(session, str(code).strip())
            identity = await self._profile(str(token["access_token"]))
            subject = str(identity.get("id") or "").strip()
            email = str(identity.get("mail") or identity.get("userPrincipalName") or "").strip()
            if not subject or not email or "@" not in email:
                raise MicrosoftProviderError("Microsoft account identity is incomplete")
            scope_text = str(token.get("scope") or "").strip()
            granted = tuple(dict.fromkeys(scope_text.split()))
            required = {SCOPE_USER_READ, SCOPE_MAIL_READ_WRITE, SCOPE_MAIL_SEND}
            if not required.issubset(set(granted)):
                raise MicrosoftProviderError(
                    "Microsoft did not grant the required mail permissions"
                )
            refresh_token = str(token.get("refresh_token") or "")
            if not refresh_token:
                raise MicrosoftProviderError("Microsoft did not grant durable offline access")
            credentials = {
                "access_token": str(token["access_token"]),
                "refresh_token": refresh_token,
                "token_type": str(token.get("token_type") or "Bearer"),
                "expires_at": _iso(
                    _utc_now() + timedelta(seconds=max(1, int(token.get("expires_in") or 3600)))
                ),
                "scopes": list(granted),
            }
            account_id = await self.accounts.upsert_account(
                provider=MICROSOFT_PROVIDER,
                principal_id=session.principal_id,
                provider_subject=subject,
                display_name=str(identity.get("displayName") or email),
                email=email,
                scopes=granted,
                credentials=credentials,
            )
            await self.accounts.complete_oauth_session(session.session_id, account_id)
            completed = await self.accounts.oauth_session(
                session.session_id, principal_id=session.principal_id
            )
            if completed is None:
                raise RuntimeError("Completed OAuth session disappeared")
            return completed
        except Exception as exc:
            await self.accounts.fail_oauth_session(
                session.session_id, redact_text(exc, max_length=500)
            )
            raise

    async def _exchange_code(self, session: OAuthSession, code: str) -> Mapping[str, Any]:
        response = await self.client.post(
            self.config.token_endpoint,
            data={
                "client_id": self.config.client_id.strip(),
                "client_secret": self.config.client_secret.strip(),
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.config.redirect_uri.strip(),
                "code_verifier": session.code_verifier or "",
                "scope": " ".join(session.requested_scopes),
            },
            headers={"Accept": "application/json"},
        )
        payload = self._json(response)
        if response.status_code != 200 or not isinstance(payload.get("access_token"), str):
            raise MicrosoftProviderError(
                "Microsoft token exchange failed", status_code=response.status_code
            )
        return payload

    async def _profile(self, token: str) -> Mapping[str, Any]:
        response = await self.client.get(
            f"{GRAPH_API}/me",
            params={"$select": "id,displayName,mail,userPrincipalName"},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        payload = self._json(response)
        if response.status_code != 200:
            raise MicrosoftProviderError(
                "Microsoft identity verification failed", status_code=response.status_code
            )
        return payload

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            value = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise MicrosoftProviderError("Microsoft returned malformed JSON") from exc
        if not isinstance(value, dict):
            raise MicrosoftProviderError("Microsoft returned a malformed response")
        return value


class MicrosoftTokenManager:
    def __init__(
        self,
        *,
        config: MicrosoftOAuthConfig,
        accounts: IntegrationAccountStore,
        client: httpx.AsyncClient,
    ) -> None:
        self.config = config
        self.accounts = accounts
        self.client = client

    async def access_token(self, principal_id: str, *, force_refresh: bool = False) -> str:
        row = await self.accounts.account(principal_id=principal_id, provider=MICROSOFT_PROVIDER)
        if row is None or not bool(row["authenticated"]):
            raise MicrosoftProviderError("Microsoft account needs reconnecting", status_code=401)
        credentials = await self.accounts.account_credentials(str(row["account_id"]))
        token = str(credentials.get("access_token") or "")
        expires_at = self._expiry(credentials.get("expires_at"))
        if token and expires_at > _utc_now() + timedelta(seconds=60) and not force_refresh:
            return token
        refresh_token = str(credentials.get("refresh_token") or "")
        if not refresh_token:
            raise MicrosoftProviderError("Microsoft account needs reconnecting", status_code=401)
        response = await self.client.post(
            self.config.token_endpoint,
            data={
                "client_id": self.config.client_id.strip(),
                "client_secret": self.config.client_secret.strip(),
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": " ".join(DEFAULT_MICROSOFT_SCOPES),
            },
            headers={"Accept": "application/json"},
        )
        payload = MicrosoftOAuthService._json(response)
        if response.status_code != 200 or not isinstance(payload.get("access_token"), str):
            error = str(payload.get("error") or "")
            reauth = (
                error in {"invalid_grant", "interaction_required"} or response.status_code == 401
            )
            if reauth:
                await self.accounts.mark_health(
                    str(row["account_id"]),
                    authenticated=False,
                    healthy=False,
                    reason="Microsoft account needs reconnecting",
                    reauthorization_required=True,
                )
            raise MicrosoftProviderError(
                "Microsoft token refresh failed",
                status_code=401 if reauth else response.status_code,
            )
        scopes = tuple(dict.fromkeys(str(payload.get("scope") or "").split()))
        updated = {
            **credentials,
            "access_token": str(payload["access_token"]),
            "refresh_token": str(payload.get("refresh_token") or refresh_token),
            "expires_at": _iso(
                _utc_now() + timedelta(seconds=max(1, int(payload.get("expires_in") or 3600)))
            ),
            "scopes": list(scopes or credentials.get("scopes") or ()),
        }
        await self.accounts.update_credentials(
            str(row["account_id"]), updated, scopes=scopes or None
        )
        return str(payload["access_token"])

    @staticmethod
    def _expiry(value: Any) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _capabilities() -> tuple[CapabilityMetadata, ...]:
    def read(capability_id: str, name: str, *, repeatable: bool = False) -> CapabilityMetadata:
        return CapabilityMetadata(
            capability_id=capability_id,
            provider_id=MICROSOFT_PROVIDER,
            name=name,
            access=CapabilityAccess.READ,
            required_scopes=frozenset({SCOPE_MAIL_READ_WRITE}),
            verification=VerificationMode.REQUIRED,
            repeatable=repeatable,
            minimum_poll_interval_seconds=300 if repeatable else None,
            maximum_monitor_polls=2016 if repeatable else None,
            monitor_ttl_seconds=30 * 86400 if repeatable else None,
            monitor_value_paths=("messages", "delta_link") if repeatable else (),
            timeout_seconds=30,
        )

    def write(capability_id: str, name: str, *, send: bool = False) -> CapabilityMetadata:
        return CapabilityMetadata(
            capability_id=capability_id,
            provider_id=MICROSOFT_PROVIDER,
            name=name,
            access=CapabilityAccess.WRITE,
            required_scopes=frozenset({SCOPE_MAIL_SEND if send else SCOPE_MAIL_READ_WRITE}),
            confirmation=ConfirmationMode.REQUIRED,
            risk=RiskLevel.HIGH,
            verification=VerificationMode.REQUIRED,
            timeout_seconds=30,
        )

    return (
        read("outlook.account", "Read Outlook account identity"),
        read("outlook.folders", "List Outlook mail folders"),
        read("outlook.search", "Search Outlook messages", repeatable=True),
        read("outlook.changes", "Read incremental Outlook Inbox changes", repeatable=True),
        read("outlook.read", "Read an Outlook message"),
        read("outlook.thread", "Read an Outlook conversation", repeatable=True),
        write("outlook.draft", "Create Outlook draft"),
        write("outlook.reply", "Create Outlook reply draft"),
        write("outlook.send", "Send Outlook draft", send=True),
        write("outlook.archive", "Move Outlook message to Archive"),
        write("outlook.trash", "Move Outlook message to Deleted Items"),
        write("outlook.restore", "Restore Outlook message from Deleted Items"),
        write("outlook.mark_read", "Mark Outlook message read"),
        write("outlook.mark_unread", "Mark Outlook message unread"),
    )


class MicrosoftConnector(Connector):
    provider_id = MICROSOFT_PROVIDER

    def __init__(
        self,
        *,
        oauth: MicrosoftOAuthService,
        accounts: IntegrationAccountStore,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30,
    ) -> None:
        super().__init__(
            provider_id=self.provider_id,
            name="Microsoft Outlook",
            capabilities=_capabilities(),
        )
        self.oauth = oauth
        self.accounts = accounts
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(max(5.0, min(float(timeout_seconds), 60.0))),
            follow_redirects=False,
            trust_env=False,
        )
        self.tokens = MicrosoftTokenManager(
            config=oauth.config, accounts=accounts, client=self.client
        )
        self._verified_capabilities: dict[str, tuple[str, ...]] = {}

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    def _unconfigured(self, reason: str) -> ProviderStatus:
        return ProviderStatus(
            provider_id=self.provider_id,
            name=self.name,
            configured=False,
            authenticated=False,
            healthy=False,
            health_reason=reason,
            setup_requirements=self.oauth.setup_requirements or ("Connect Outlook",),
            potential_capabilities=tuple(item.capability_id for item in self.capabilities),
            executable_capabilities=(),
        )

    async def status(self) -> ProviderStatus:
        return self._unconfigured("Outlook status is principal-scoped")

    async def status_for_principal(self, principal_id: str | None) -> ProviderStatus:
        principal = str(principal_id or "").strip()
        if not self.oauth.configured:
            return self._unconfigured("Microsoft OAuth setup is incomplete")
        if not principal:
            return self._unconfigured("An authenticated account owner is required")
        row = await self.accounts.account(principal_id=principal, provider=MICROSOFT_PROVIDER)
        if row is None:
            return self._unconfigured("Outlook is not connected")
        scopes = frozenset(json.loads(str(row["scopes_json"])))
        if not bool(row["authenticated"]):
            return ProviderStatus(
                provider_id=self.provider_id,
                name=self.name,
                configured=True,
                authenticated=False,
                healthy=False,
                health_reason=row["health_reason"] or "Outlook needs reconnecting",
                setup_requirements=("Reconnect Outlook",),
                scopes=scopes,
                potential_capabilities=tuple(item.capability_id for item in self.capabilities),
                executable_capabilities=(),
            )
        try:
            identity = await self._request(
                principal,
                "GET",
                f"{GRAPH_API}/me",
                params={"$select": "id,displayName,mail,userPrincipalName"},
            )
            if str(identity.get("id") or "") != str(row["provider_subject"]):
                raise MicrosoftProviderError("Microsoft account identity changed", status_code=401)
            await self._request(
                principal,
                "GET",
                f"{GRAPH_API}/me/mailFolders/inbox",
                params={"$select": "id,displayName,totalItemCount,unreadItemCount"},
            )
            executable = tuple(
                item.capability_id
                for item in self.capabilities
                if item.required_scopes.issubset(scopes)
            )
            self._verified_capabilities[principal] = executable
            await self.accounts.mark_health(
                str(row["account_id"]), authenticated=True, healthy=True, reason=None
            )
            return ProviderStatus(
                provider_id=self.provider_id,
                name=self.name,
                configured=True,
                authenticated=True,
                healthy=True,
                scopes=scopes,
                potential_capabilities=tuple(item.capability_id for item in self.capabilities),
                executable_capabilities=executable,
            )
        except MicrosoftProviderError as exc:
            reauth = exc.reauthorization_required
            reason = "Outlook needs reconnecting" if reauth else redact_text(exc, max_length=500)
            await self.accounts.mark_health(
                str(row["account_id"]),
                authenticated=not reauth,
                healthy=False,
                reason=reason,
                reauthorization_required=reauth,
            )
            return ProviderStatus(
                provider_id=self.provider_id,
                name=self.name,
                configured=True,
                authenticated=not reauth,
                healthy=False,
                health_reason=reason,
                setup_requirements=(("Reconnect Outlook",) if reauth else ("Retry later",)),
                scopes=scopes,
                potential_capabilities=tuple(item.capability_id for item in self.capabilities),
                executable_capabilities=(),
            )

    async def account_status(self, principal_id: str) -> IntegrationAccount | None:
        row = await self.accounts.account(principal_id=principal_id, provider=MICROSOFT_PROVIDER)
        if row is None:
            return None
        scopes = frozenset(json.loads(str(row["scopes_json"])))
        available = self._verified_capabilities.get(str(principal_id).strip(), ())
        definitions = {item.capability_id: item for item in self.capabilities}
        return IntegrationAccount(
            provider=MICROSOFT_PROVIDER,
            account_id=str(row["account_id"]),
            principal_id=str(row["principal_id"]),
            provider_subject=str(row["provider_subject"]),
            account_display_name=str(row["display_name"]),
            account_email=row["email"],
            configured=True,
            authenticated=bool(row["authenticated"]),
            healthy=bool(row["healthy"]),
            granted_scopes=tuple(sorted(scopes)),
            available_capabilities=available,
            read_capabilities=tuple(
                item for item in available if definitions[item].access is CapabilityAccess.READ
            ),
            write_capabilities=tuple(
                item for item in available if definitions[item].access is CapabilityAccess.WRITE
            ),
            last_health_check=row["last_health_check"],
            reauthorization_required=bool(row["reauthorization_required"]),
            setup_requirements=(
                ("Reconnect Outlook",) if bool(row["reauthorization_required"]) else ()
            ),
            health_reason=row["health_reason"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    async def credential_status(self, principal_id: str) -> dict[str, Any] | None:
        row = await self.accounts.account(principal_id=principal_id, provider=MICROSOFT_PROVIDER)
        if row is None:
            return None
        try:
            return await self.accounts.credential_status(str(row["account_id"]))
        except (CredentialEncryptionUnavailable, ValueError):
            return {
                "access_token_present": False,
                "refresh_token_present": False,
                "expires_at": None,
                "expired": False,
                "expires_soon": False,
                "error_category": "credential_unavailable",
            }

    async def disconnect(self, *, principal_id: str, account_id: str) -> dict[str, Any]:
        row = await self.accounts.account(
            principal_id=principal_id, provider=MICROSOFT_PROVIDER, account_id=account_id
        )
        if row is None:
            return {"disconnected": False}
        return {
            "disconnected": await self.accounts.delete_account(
                principal_id=principal_id, account_id=account_id
            )
        }

    async def execute(
        self, capability: CapabilityMetadata, request: CapabilityRequest
    ) -> ConnectorResult:
        principal = str(request.principal_id or "").strip()
        if not principal:
            return ConnectorResult.failed("An authenticated account owner is required")
        handlers = {
            "outlook.account": self._account,
            "outlook.folders": self._folders,
            "outlook.search": self._search,
            "outlook.changes": self._changes,
            "outlook.read": self._read,
            "outlook.thread": self._thread,
            "outlook.draft": self._draft,
            "outlook.reply": self._reply,
            "outlook.send": self._send,
            "outlook.archive": self._archive,
            "outlook.trash": self._trash,
            "outlook.restore": self._restore,
            "outlook.mark_read": self._mark_read,
            "outlook.mark_unread": self._mark_unread,
        }
        handler = handlers.get(capability.capability_id)
        if handler is None:
            return ConnectorResult.failed("Unsupported Outlook capability")
        try:
            data, reference = await handler(principal, dict(request.payload))
            return ConnectorResult.succeeded(data, provider_reference=reference)
        except MicrosoftProviderError as exc:
            safe = redact_text(exc, max_length=800)
            if capability.access is CapabilityAccess.WRITE and exc.outcome_unknown:
                return ConnectorResult.outcome_unknown(safe)
            return ConnectorResult.failed(safe, retryable=exc.retryable)
        except (ValueError, KeyError) as exc:
            return ConnectorResult.failed(redact_text(exc, max_length=800))

    async def verify(
        self, capability: CapabilityMetadata, request: CapabilityRequest, result: ConnectorResult
    ) -> VerificationResult:
        principal = str(request.principal_id or "").strip()
        reference = str(result.provider_reference or "").strip()
        if not principal or not reference:
            return VerificationResult.unverified("Provider reference is missing")
        try:
            observed = await self._request(
                principal,
                "GET",
                f"{GRAPH_API}/me/messages/{self._segment(reference)}",
                params={
                    "$select": "id,isDraft,isRead,parentFolderId,subject,toRecipients,conversationId"
                },
            )
            capability_id = capability.capability_id
            if str(observed.get("id") or "") != reference:
                return VerificationResult.unverified("Outlook message verification mismatch")
            if (
                capability_id in {"outlook.draft", "outlook.reply"}
                and observed.get("isDraft") is not True
            ):
                return VerificationResult.unverified("Outlook draft was not verified")
            if capability_id == "outlook.send" and observed.get("isDraft") is True:
                return VerificationResult.unverified("Outlook message is still a draft")
            if capability_id in {"outlook.archive", "outlook.trash", "outlook.restore"}:
                expected = str(result.data.get("destination_id") or "")
                if not expected or str(observed.get("parentFolderId") or "") != expected:
                    return VerificationResult.unverified("Outlook destination folder did not match")
            if capability_id == "outlook.mark_read" and observed.get("isRead") is not True:
                return VerificationResult.unverified("Outlook message is still unread")
            if capability_id == "outlook.mark_unread" and observed.get("isRead") is not False:
                return VerificationResult.unverified("Outlook message is still read")
        except MicrosoftProviderError as exc:
            return VerificationResult.unverified(str(exc))
        return VerificationResult.verified(
            {
                "provider": MICROSOFT_PROVIDER,
                "capability_id": capability.capability_id,
                "provider_reference": reference,
                "verified_at": _iso(),
            }
        )

    async def _raw_request(
        self,
        principal: str,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "graph.microsoft.com":
            raise MicrosoftProviderError("Microsoft Graph continuation URL was rejected")
        token = await self.tokens.access_token(principal)
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            # Immutable IDs keep exact audit/focus identities stable when a
            # message is moved between Inbox, Archive and Deleted Items.
            "Prefer": 'IdType="ImmutableId", outlook.body-content-type="text"',
        }
        try:
            response = await self.client.request(
                method, url, params=params, json=json_body, headers=headers
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise MicrosoftProviderError(
                "Microsoft Graph transport failed",
                outcome_unknown=method.upper() not in {"GET", "HEAD", "OPTIONS"},
                retryable=True,
            ) from exc
        if response.status_code == 401:
            token = await self.tokens.access_token(principal, force_refresh=True)
            headers["Authorization"] = f"Bearer {token}"
            response = await self.client.request(
                method, url, params=params, json=json_body, headers=headers
            )
        return response

    async def _request(
        self,
        principal: str,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        expected: Sequence[int] = (200,),
    ) -> dict[str, Any]:
        response = await self._raw_request(
            principal, method, url, params=params, json_body=json_body
        )
        if response.status_code not in expected:
            raise MicrosoftProviderError(
                f"Microsoft Graph request failed with HTTP {response.status_code}",
                status_code=response.status_code,
                outcome_unknown=(
                    method.upper() not in {"GET", "HEAD", "OPTIONS"} and response.status_code >= 500
                ),
            )
        if response.status_code in {202, 204} or not response.content:
            return {}
        return MicrosoftOAuthService._json(response)

    @staticmethod
    def _segment(value: str) -> str:
        return quote(str(value), safe="")

    @staticmethod
    def _required(payload: Mapping[str, Any], key: str, *, limit: int = 10_000) -> str:
        value = str(payload.get(key) or "").strip()
        if not value or len(value) > limit or "\x00" in value:
            raise ValueError(f"{key} is missing or invalid")
        return value

    @staticmethod
    def _recipient(value: str) -> str:
        addresses = getaddresses([value])
        address = addresses[0][1].strip() if len(addresses) == 1 else ""
        if not address or address.casefold() != value.strip().casefold() or "@" not in address:
            raise ValueError("An exact recipient email address is required")
        local, _, domain = address.rpartition("@")
        if not local or "." not in domain or len(address) > 320:
            raise ValueError("An exact recipient email address is required")
        return address

    @staticmethod
    def _body_text(value: Any) -> str:
        text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
        return " ".join(text.split())[:20_000]

    @classmethod
    def _message(cls, item: Mapping[str, Any]) -> dict[str, Any]:
        raw_sender = item.get("from")
        sender = dict(raw_sender) if isinstance(raw_sender, Mapping) else {}
        raw_sender_email = sender.get("emailAddress")
        sender_email = dict(raw_sender_email) if isinstance(raw_sender_email, Mapping) else {}
        raw_body = item.get("body")
        body = dict(raw_body) if isinstance(raw_body, Mapping) else {}
        labels: list[str] = ["OUTLOOK"]
        if item.get("isRead") is False:
            labels.append("UNREAD")
        if str(item.get("importance") or "").casefold() == "high":
            labels.append("IMPORTANT")
        raw_flag = item.get("flag")
        flag = dict(raw_flag) if isinstance(raw_flag, Mapping) else {}
        if str(flag.get("flagStatus") or "").casefold() == "flagged":
            labels.append("STARRED")
        recipients: list[str] = []
        for raw in item.get("toRecipients") or ():
            if not isinstance(raw, Mapping):
                continue
            email_address = raw.get("emailAddress")
            if isinstance(email_address, Mapping) and email_address.get("address"):
                recipients.append(str(email_address["address"]))
        timestamp = str(item.get("receivedDateTime") or item.get("sentDateTime") or "")
        try:
            epoch_ms = int(
                datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp() * 1000
            )
        except ValueError:
            epoch_ms = None
        return {
            "provider": "microsoft_outlook",
            "message_id": item.get("id"),
            "thread_id": item.get("conversationId"),
            "conversation_id": item.get("conversationId"),
            "internet_message_id": item.get("internetMessageId"),
            "from": str(sender_email.get("address") or ""),
            "sender_name": str(sender_email.get("name") or ""),
            "to": ", ".join(recipients),
            "subject": str(item.get("subject") or ""),
            "snippet": cls._body_text(item.get("bodyPreview")),
            "body": cls._body_text(body.get("content")),
            "internal_date_ms": epoch_ms,
            "received_at": timestamp,
            "label_ids": labels,
            "attachments": ([{"present": True}] if item.get("hasAttachments") else []),
            "list_unsubscribe": " unsubscribe "
            in f" {cls._body_text(body.get('content')).casefold()} ",
            "parent_folder_id": item.get("parentFolderId"),
            "has_attachments": bool(item.get("hasAttachments")),
            "is_draft": bool(item.get("isDraft")),
        }

    @staticmethod
    def _select() -> str:
        return (
            "id,conversationId,internetMessageId,parentFolderId,subject,body,bodyPreview,"
            "from,toRecipients,receivedDateTime,sentDateTime,isRead,isDraft,importance,flag,hasAttachments"
        )

    async def _account(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        del payload
        value = await self._request(
            principal,
            "GET",
            f"{GRAPH_API}/me",
            params={"$select": "id,displayName,mail,userPrincipalName"},
        )
        return {
            "account_id": value.get("id"),
            "display_name": value.get("displayName"),
            "email": value.get("mail") or value.get("userPrincipalName"),
        }, str(value.get("id") or "") or None

    async def _folders(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        del payload
        value = await self._request(
            principal,
            "GET",
            f"{GRAPH_API}/me/mailFolders",
            params={
                "$select": "id,displayName,parentFolderId,totalItemCount,unreadItemCount",
                "$top": 100,
            },
        )
        folders = [dict(item) for item in value.get("value") or () if isinstance(item, Mapping)]
        return {"folders": folders, "count": len(folders)}, None

    async def _folder_id(self, principal: str, well_known: str) -> str:
        value = await self._request(
            principal,
            "GET",
            f"{GRAPH_API}/me/mailFolders/{self._segment(well_known)}",
            params={"$select": "id,displayName"},
        )
        folder_id = str(value.get("id") or "")
        if not folder_id:
            raise MicrosoftProviderError("Microsoft returned an invalid mail folder")
        return folder_id

    async def _search(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        query = str(payload.get("query") or "").strip()
        if len(query) > 1000:
            raise ValueError("query is invalid")
        limit = max(1, min(int(payload.get("limit") or 20), 100))
        all_pages = payload.get("all_pages") is True
        maximum = max(1, min(int(payload.get("max_messages") or 5_000), 10_000))
        folder = str(payload.get("folder") or "").strip()
        path = f"/me/mailFolders/{self._segment(folder)}/messages" if folder else "/me/messages"
        initial_params: dict[str, Any] = {"$top": limit, "$select": self._select()}
        if query:
            initial_params["$search"] = f'"{query.replace(chr(34), "")}"'
        if payload.get("unread") is True:
            if query:
                raise ValueError("Outlook unread filtering cannot be combined with text search")
            initial_params["$filter"] = "isRead eq false"
        params: Mapping[str, Any] | None = initial_params
        messages: list[dict[str, Any]] = []
        url = GRAPH_API + path
        pages = 0
        next_link = ""
        while pages < 100 and len(messages) < maximum:
            pages += 1
            value = await self._request(principal, "GET", url, params=params)
            messages.extend(
                self._message(item)
                for item in value.get("value") or ()
                if isinstance(item, Mapping)
            )
            next_link = str(value.get("@odata.nextLink") or "").strip()
            if not all_pages or not next_link:
                break
            url, params = next_link, None
        if len(messages) > maximum:
            messages = messages[:maximum]
        if folder.casefold() == "inbox":
            for message in messages:
                message["label_ids"] = [*message["label_ids"], "INBOX"]
        elif folder.casefold() == "sentitems":
            for message in messages:
                message["label_ids"] = [*message["label_ids"], "SENT"]
        elif folder.casefold() == "deleteditems":
            for message in messages:
                message["label_ids"] = [*message["label_ids"], "TRASH"]
        return {
            "messages": messages,
            "message_ids": [str(item["message_id"]) for item in messages],
            "count": len(messages),
            "pages": pages,
            "truncated": bool(next_link),
        }, None

    async def _changes(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        delta_link = str(payload.get("delta_link") or "").strip()
        bootstrap = not delta_link
        if delta_link:
            url = delta_link
            params = None
        else:
            url = f"{GRAPH_API}/me/mailFolders/inbox/messages/delta"
            since = _iso(_utc_now() - timedelta(days=1)).replace("+00:00", "Z")
            params = {
                "$filter": f"receivedDateTime ge {since}",
                "$select": self._select(),
                "$top": max(1, min(int(payload.get("limit") or 100), 100)),
            }
        messages: list[dict[str, Any]] = []
        pages = 0
        final_link = ""
        while pages < 20:
            pages += 1
            try:
                value = await self._request(principal, "GET", url, params=params)
            except MicrosoftProviderError as exc:
                if delta_link and exc.status_code == 410:
                    fresh, reference = await self._changes(
                        principal, {"limit": payload.get("limit") or 100}
                    )
                    fresh["cursor_expired"] = True
                    fresh["previous_delta_link"] = delta_link
                    return fresh, reference
                raise
            if not bootstrap:
                for item in value.get("value") or ():
                    if not isinstance(item, Mapping) or "@removed" in item:
                        continue
                    message = self._message(item)
                    message["label_ids"] = [*message["label_ids"], "INBOX"]
                    messages.append(message)
            next_link = str(value.get("@odata.nextLink") or "")
            final_link = str(value.get("@odata.deltaLink") or final_link)
            if not next_link:
                break
            url, params = next_link, None
        if not final_link:
            raise MicrosoftProviderError("Microsoft Graph delta cursor is missing")
        account, _ = await self._account(principal, {})
        return {
            "provider": "microsoft_outlook",
            "account_email": account.get("email"),
            "delta_link": final_link,
            "messages": messages,
            "bootstrap": bootstrap,
            "cursor_expired": False,
        }, None

    async def _read(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        message_id = self._required(payload, "message_id", limit=1000)
        value = await self._request(
            principal,
            "GET",
            f"{GRAPH_API}/me/messages/{self._segment(message_id)}",
            params={"$select": self._select()},
        )
        return {"message": self._message(value)}, message_id

    async def _thread(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        conversation_id = self._required(payload, "conversation_id", limit=1000)
        escaped = conversation_id.replace("'", "''")
        value = await self._request(
            principal,
            "GET",
            f"{GRAPH_API}/me/messages",
            params={
                "$filter": f"conversationId eq '{escaped}'",
                "$select": self._select(),
                "$top": max(1, min(int(payload.get("limit") or 50), 100)),
            },
        )
        messages = [
            self._message(item) for item in value.get("value") or () if isinstance(item, Mapping)
        ]
        return {
            "conversation_id": conversation_id,
            "messages": messages,
            "count": len(messages),
        }, conversation_id

    async def _draft(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        recipient = self._recipient(self._required(payload, "to", limit=320))
        subject = (
            self._required(payload, "subject", limit=998).replace("\r", " ").replace("\n", " ")
        )
        body = self._required(payload, "body", limit=100_000)
        value = await self._request(
            principal,
            "POST",
            f"{GRAPH_API}/me/messages",
            json_body={
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": recipient}}],
            },
            expected=(201,),
        )
        message_id = str(value.get("id") or "")
        if not message_id:
            raise MicrosoftProviderError("Microsoft did not return a draft ID")
        return {
            "draft_id": message_id,
            "message_id": message_id,
            "recipient": recipient,
            "subject": subject,
            "thread_id": value.get("conversationId"),
            "status": "drafted",
        }, message_id

    async def _reply(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        message_id = self._required(payload, "message_id", limit=1000)
        body = self._required(payload, "body", limit=100_000)
        draft = await self._request(
            principal,
            "POST",
            f"{GRAPH_API}/me/messages/{self._segment(message_id)}/createReply",
            expected=(201,),
        )
        draft_id = str(draft.get("id") or "")
        if not draft_id:
            raise MicrosoftProviderError("Microsoft did not return a reply draft ID")
        updated = await self._request(
            principal,
            "PATCH",
            f"{GRAPH_API}/me/messages/{self._segment(draft_id)}",
            json_body={"body": {"contentType": "Text", "content": body}},
        )
        return {
            "draft_id": draft_id,
            "message_id": draft_id,
            "thread_id": updated.get("conversationId") or draft.get("conversationId"),
            "status": "reply_drafted",
        }, draft_id

    async def _send(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        draft_id = self._required(payload, "draft_id", limit=1000)
        before = await self._request(
            principal,
            "GET",
            f"{GRAPH_API}/me/messages/{self._segment(draft_id)}",
            params={"$select": "id,internetMessageId,subject,toRecipients,conversationId,isDraft"},
        )
        internet_id = str(before.get("internetMessageId") or "").strip()
        await self._request(
            principal,
            "POST",
            f"{GRAPH_API}/me/messages/{self._segment(draft_id)}/send",
            expected=(202,),
        )
        if not internet_id:
            raise MicrosoftProviderError(
                "Microsoft accepted the send but did not provide an identity for verification",
                outcome_unknown=True,
            )
        escaped = internet_id.replace("'", "''")
        sent = await self._request(
            principal,
            "GET",
            f"{GRAPH_API}/me/mailFolders/sentitems/messages",
            params={
                "$filter": f"internetMessageId eq '{escaped}'",
                "$select": "id,isDraft,subject,toRecipients,conversationId",
                "$top": 2,
            },
        )
        matches = [item for item in sent.get("value") or () if isinstance(item, Mapping)]
        if len(matches) != 1 or not matches[0].get("id"):
            raise MicrosoftProviderError(
                "Microsoft send verification was inconclusive", outcome_unknown=True
            )
        observed = matches[0]
        sent_id = str(observed["id"])
        return {
            "message_id": sent_id,
            "thread_id": observed.get("conversationId"),
            "subject": observed.get("subject"),
            "status": "sent",
        }, sent_id

    async def _move(
        self, principal: str, payload: dict[str, Any], destination: str
    ) -> tuple[dict[str, Any], str | None]:
        message_id = self._required(payload, "message_id", limit=1000)
        destination_id = (
            await self._folder_id(principal, destination)
            if destination in {"archive", "deleteditems", "inbox", "sentitems", "drafts"}
            else self._required({"destination_id": destination}, "destination_id", limit=1000)
        )
        value = await self._request(
            principal,
            "POST",
            f"{GRAPH_API}/me/messages/{self._segment(message_id)}/move",
            json_body={"destinationId": destination_id},
            expected=(201,),
        )
        moved_id = str(value.get("id") or "")
        if not moved_id:
            raise MicrosoftProviderError("Microsoft did not return the moved message ID")
        return {
            "message_id": moved_id,
            "previous_message_id": message_id,
            "destination_id": destination_id,
            "parent_folder_id": value.get("parentFolderId"),
            "status": "moved",
        }, moved_id

    async def _archive(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        return await self._move(principal, payload, "archive")

    async def _trash(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        return await self._move(principal, payload, "deleteditems")

    async def _restore(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        destination_id = self._required(payload, "destination_id", limit=1000)
        return await self._move(principal, payload, destination_id)

    async def _set_read(
        self, principal: str, payload: dict[str, Any], value: bool
    ) -> tuple[dict[str, Any], str | None]:
        message_id = self._required(payload, "message_id", limit=1000)
        observed = await self._request(
            principal,
            "PATCH",
            f"{GRAPH_API}/me/messages/{self._segment(message_id)}",
            json_body={"isRead": value},
        )
        return {
            "message_id": message_id,
            "is_read": observed.get("isRead"),
            "status": "read" if value else "unread",
        }, message_id

    async def _mark_read(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        return await self._set_read(principal, payload, True)

    async def _mark_unread(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        return await self._set_read(principal, payload, False)


__all__ = [
    "DEFAULT_MICROSOFT_SCOPES",
    "GRAPH_API",
    "MICROSOFT_MODEL_TOOL",
    "MICROSOFT_PROVIDER",
    "MicrosoftConnector",
    "MicrosoftOAuthConfig",
    "MicrosoftOAuthService",
    "MicrosoftProviderError",
    "MicrosoftTokenManager",
    "microsoft_model_tool",
]
