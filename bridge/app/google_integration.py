"""Official Google OAuth, Gmail, Calendar, and People API connector."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate, getaddresses
from typing import Any, Mapping, Sequence
from urllib.parse import quote, urlencode, urlsplit

import httpx

from app.connectors.base import (
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
    CredentialEncryptionUnavailable,
    CredentialCipher,
    IntegrationAccount,
    IntegrationAccountStore,
    OAuthSession,
    OAuthSessionError,
)


GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_REVOCATION_ENDPOINT = "https://oauth2.googleapis.com/revoke"
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
PEOPLE_API = "https://people.googleapis.com/v1"

SCOPE_OPENID = "openid"
SCOPE_EMAIL = "email"
SCOPE_GMAIL_READ = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_GMAIL_MODIFY = "https://www.googleapis.com/auth/gmail.modify"
SCOPE_GMAIL_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
SCOPE_CALENDAR_READ = "https://www.googleapis.com/auth/calendar.readonly"
SCOPE_CALENDAR_WRITE = "https://www.googleapis.com/auth/calendar.events"
SCOPE_CONTACTS_READ = "https://www.googleapis.com/auth/contacts.readonly"

GOOGLE_SCOPE_FEATURES: Mapping[str, tuple[str, ...]] = {
    "gmail_read": (SCOPE_GMAIL_READ,),
    "gmail_write": (SCOPE_GMAIL_MODIFY, SCOPE_GMAIL_COMPOSE),
    "calendar_read": (SCOPE_CALENDAR_READ,),
    "calendar_write": (SCOPE_CALENDAR_READ, SCOPE_CALENDAR_WRITE),
    "contacts_read": (SCOPE_CONTACTS_READ,),
}
DEFAULT_GOOGLE_FEATURES = (
    "gmail_read",
    "gmail_write",
    "calendar_read",
    "calendar_write",
    "contacts_read",
)
GOOGLE_MODEL_TOOL = "google_integration"

_GOOGLE_ARGUMENT_GUIDANCE: Mapping[str, str] = {
    "gmail.search": "query (Gmail search syntax), optional limit",
    "gmail.read": "message_id",
    "gmail.thread": "thread_id, or query and optional limit",
    "gmail.draft": "to (verified email), subject, body; optional draft_id",
    "gmail.reply": "message_id and body; creates a draft and does not send",
    "gmail.send": "draft_id; sends the existing draft",
    "gmail.forward": "message_id, to (verified email), body",
    "gmail.archive": "message_id",
    "calendar.list": "events=true with optional calendar_id/timeMin/timeMax/limit, or limit",
    "calendar.read": "event_id and optional calendar_id",
    "calendar.search": "query with optional calendar_id/timeMin/timeMax/limit",
    "calendar.availability": "timeMin, timeMax, optional timeZone/calendar_ids",
    "calendar.timezone": "no arguments",
    "calendar.create": "summary, start object, end object; optional calendar_id/description/location/attendees",
    "calendar.update": "event_id and changes object; optional calendar_id",
    "calendar.cancel": "event_id; optional calendar_id",
    "contacts.search": "query and optional limit",
    "contacts.resolve": "query; fails closed when multiple contacts match",
}


def google_model_tool(executable_capabilities: Sequence[str]) -> dict[str, Any] | None:
    """Return one provider tool whose capability enum is live and principal-scoped."""

    capabilities = tuple(
        item
        for item in dict.fromkeys(str(value) for value in executable_capabilities)
        if item in _GOOGLE_ARGUMENT_GUIDANCE
    )
    if not capabilities:
        return None
    guidance = "; ".join(
        f"{capability}: {_GOOGLE_ARGUMENT_GUIDANCE[capability]}" for capability in capabilities
    )
    return {
        "type": "function",
        "name": GOOGLE_MODEL_TOOL,
        "description": (
            "Execute one live Google account capability through deterministic identity, "
            "scope, confirmation, idempotency, receipt and verification policy. "
            "Never invent an email, phone number, message ID, draft ID or event ID. "
            "Draft and reply create drafts only; gmail.send is the only draft-send action. "
            "For a new email the user explicitly says to send, first call gmail.draft "
            "with the stated recipient, subject and body, then call gmail.send with "
            "the verified draft_id returned by that first call. The recipient domain "
            "does not select the sender provider: a connected Gmail account may send "
            "to Outlook, Hotmail, Yahoo or another valid email domain. "
            "Argument contracts: " + guidance
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


def _decode_b64url(value: str) -> bytes:
    encoded = str(value or "")
    return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))


class GoogleProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        reauthorization_required: bool | None = None,
        outcome_unknown: bool = False,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self._reauthorization_required = reauthorization_required
        self.outcome_unknown = bool(outcome_unknown)
        self._retryable = retryable

    @property
    def reauthorization_required(self) -> bool:
        if self._reauthorization_required is not None:
            return self._reauthorization_required
        return self.status_code in {400, 401}

    @property
    def retryable(self) -> bool:
        if self._retryable is not None:
            return self._retryable
        return self.status_code == 429 or bool(self.status_code and self.status_code >= 500)


@dataclass(frozen=True, slots=True)
class GoogleOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    android_return_uri: str = "jarvis://integrations/google"

    @property
    def configured(self) -> bool:
        if not self.client_id.strip() or not self.client_secret.strip():
            return False
        try:
            parsed = urlsplit(self.redirect_uri.strip())
        except ValueError:
            return False
        secure = parsed.scheme == "https" or (
            parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
        )
        try:
            android = urlsplit(self.android_return_uri.strip())
        except ValueError:
            return False
        android_valid = bool(
            android.scheme == "jarvis"
            and android.hostname == "integrations"
            and android.path == "/google"
            and not android.username
            and not android.password
            and not android.query
            and not android.fragment
        )
        return bool(
            secure
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and android_valid
        )

    @property
    def setup_requirements(self) -> tuple[str, ...]:
        missing: list[str] = []
        if not self.client_id.strip():
            missing.append("Configure JARVIS_GOOGLE_OAUTH_CLIENT_ID")
        if not self.client_secret.strip():
            missing.append("Configure JARVIS_GOOGLE_OAUTH_CLIENT_SECRET")
        if not self.redirect_uri.strip():
            missing.append("Configure JARVIS_GOOGLE_OAUTH_REDIRECT_URI")
        else:
            try:
                redirect = urlsplit(self.redirect_uri.strip())
            except ValueError:
                redirect = None
            if not (
                redirect
                and (
                    redirect.scheme == "https"
                    or (
                        redirect.scheme == "http"
                        and redirect.hostname in {"127.0.0.1", "localhost"}
                    )
                )
                and redirect.hostname
                and not redirect.username
                and not redirect.password
                and not redirect.query
                and not redirect.fragment
            ):
                missing.append("Use an exact HTTPS or loopback Google OAuth redirect URI")
        try:
            android = urlsplit(self.android_return_uri.strip())
        except ValueError:
            android = None
        if not (
            android
            and android.scheme == "jarvis"
            and android.hostname == "integrations"
            and android.path == "/google"
            and not android.username
            and not android.password
            and not android.query
            and not android.fragment
        ):
            missing.append("Use the exact Android return URI jarvis://integrations/google")
        return tuple(missing)


class GoogleOAuthService:
    """Authorization Code + PKCE flow whose tokens terminate at Core."""

    def __init__(
        self,
        *,
        config: GoogleOAuthConfig,
        accounts: IntegrationAccountStore,
        cipher: CredentialCipher,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
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
        requirements = list(self.config.setup_requirements)
        if not self.cipher.configured:
            requirements.append("Configure JARVIS_CREDENTIAL_ENCRYPTION_KEY")
        return tuple(requirements)

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    @staticmethod
    def scopes_for_features(features: Sequence[str]) -> tuple[str, ...]:
        chosen = tuple(dict.fromkeys(str(item).strip() for item in features if item))
        unknown = sorted(set(chosen) - set(GOOGLE_SCOPE_FEATURES))
        if unknown:
            raise ValueError("Unsupported Google permission feature: " + ", ".join(unknown))
        scopes = [SCOPE_OPENID, SCOPE_EMAIL]
        for feature in chosen:
            scopes.extend(GOOGLE_SCOPE_FEATURES[feature])
        return tuple(dict.fromkeys(scopes))

    async def start(
        self,
        *,
        principal_id: str,
        features: Sequence[str] = DEFAULT_GOOGLE_FEATURES,
    ) -> dict[str, Any]:
        if not self.configured:
            raise RuntimeError("Google OAuth setup is incomplete")
        principal = str(principal_id or "").strip()
        if not principal:
            raise ValueError("An authenticated principal is required")
        scopes = self.scopes_for_features(features)
        state = secrets.token_urlsafe(48)
        verifier = secrets.token_urlsafe(64)
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        session = await self.accounts.create_oauth_session(
            provider="google",
            principal_id=principal,
            redirect_uri=self.config.redirect_uri.strip(),
            requested_scopes=scopes,
            state=state,
            code_verifier=verifier,
        )
        query = urlencode(
            {
                "client_id": self.config.client_id.strip(),
                "redirect_uri": self.config.redirect_uri.strip(),
                "response_type": "code",
                "scope": " ".join(scopes),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "access_type": "offline",
                "include_granted_scopes": "true",
                "prompt": "consent",
            }
        )
        return {
            "session": session.as_dict(),
            "authorization_url": f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{query}",
        }

    async def callback(
        self,
        *,
        state: str,
        code: str | None,
        provider_error: str | None = None,
    ) -> OAuthSession:
        session = await self.accounts.claim_oauth_callback(provider="google", state=state)
        if provider_error:
            message = "Google authorization was cancelled or denied"
            await self.accounts.fail_oauth_session(session.session_id, message)
            raise OAuthSessionError(message)
        if not code or not str(code).strip() or len(str(code)) > 4_096:
            await self.accounts.fail_oauth_session(
                session.session_id, "Google did not return an authorization code"
            )
            raise OAuthSessionError("Google did not return an authorization code")
        if session.redirect_uri != self.config.redirect_uri.strip():
            await self.accounts.fail_oauth_session(
                session.session_id, "OAuth redirect URI validation failed"
            )
            raise OAuthSessionError("OAuth redirect URI validation failed")
        try:
            token_payload = await self._exchange_code(session, str(code).strip())
            identity = await self._userinfo(str(token_payload["access_token"]))
            if identity.get("email_verified") is not True:
                raise GoogleProviderError("Google account email is not verified")
            subject = str(identity.get("sub") or "").strip()
            email = str(identity.get("email") or "").strip()
            if not subject or not email:
                raise GoogleProviderError("Google account identity is incomplete")
            scope_text = str(token_payload.get("scope") or "").strip()
            if not scope_text:
                raise GoogleProviderError("Google did not return a verifiable granted-scope list")
            granted = tuple(dict.fromkeys(scope_text.split()))
            credentials = {
                "access_token": str(token_payload["access_token"]),
                "refresh_token": str(token_payload.get("refresh_token") or ""),
                "token_type": str(token_payload.get("token_type") or "Bearer"),
                "expires_at": _iso(
                    _utc_now()
                    + timedelta(seconds=max(1, int(token_payload.get("expires_in") or 3600)))
                ),
                "scopes": list(granted),
            }
            existing = await self.accounts.account(
                principal_id=session.principal_id,
                provider="google",
            )
            if (
                existing is not None
                and existing["provider_subject"] == subject
                and not credentials["refresh_token"]
            ):
                previous = await self.accounts.account_credentials(existing["account_id"])
                credentials["refresh_token"] = str(previous.get("refresh_token") or "")
            if not credentials["refresh_token"]:
                raise GoogleProviderError(
                    "Google did not issue durable offline access; reconnect with consent"
                )
            account_id = await self.accounts.upsert_account(
                provider="google",
                principal_id=session.principal_id,
                provider_subject=subject,
                display_name=str(identity.get("name") or email),
                email=email,
                scopes=granted,
                credentials=credentials,
            )
            await self.accounts.complete_oauth_session(session.session_id, account_id)
            completed = await self.accounts.oauth_session(
                session.session_id,
                principal_id=session.principal_id,
            )
            if completed is None:  # pragma: no cover - durable invariant
                raise RuntimeError("Completed OAuth session disappeared")
            return completed
        except Exception as exc:
            await self.accounts.fail_oauth_session(
                session.session_id,
                redact_text(exc, max_length=500),
            )
            raise

    async def _exchange_code(self, session: OAuthSession, code: str) -> Mapping[str, Any]:
        response = await self.client.post(
            GOOGLE_TOKEN_ENDPOINT,
            data={
                "client_id": self.config.client_id.strip(),
                "client_secret": self.config.client_secret.strip(),
                "code": code,
                "code_verifier": session.code_verifier or "",
                "grant_type": "authorization_code",
                "redirect_uri": self.config.redirect_uri.strip(),
            },
            headers={"Accept": "application/json"},
        )
        payload = self._safe_json(response)
        if response.status_code != 200:
            raise GoogleProviderError(
                "Google token exchange failed",
                status_code=response.status_code,
            )
        if not isinstance(payload.get("access_token"), str):
            raise GoogleProviderError("Google token response was malformed")
        return payload

    async def _userinfo(self, access_token: str) -> Mapping[str, Any]:
        response = await self.client.get(
            GOOGLE_USERINFO_ENDPOINT,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )
        payload = self._safe_json(response)
        if response.status_code != 200:
            raise GoogleProviderError(
                "Google identity verification failed",
                status_code=response.status_code,
            )
        return payload

    @staticmethod
    def _safe_json(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise GoogleProviderError("Google returned malformed JSON") from exc
        if not isinstance(payload, dict):
            raise GoogleProviderError("Google returned a malformed response")
        return payload


def _capabilities() -> tuple[CapabilityMetadata, ...]:
    provider = "google"

    def read(
        capability_id: str,
        name: str,
        scopes: Sequence[str],
        *,
        repeatable: bool = False,
        value_paths: tuple[str, ...] = (),
    ) -> CapabilityMetadata:
        return CapabilityMetadata(
            capability_id=capability_id,
            provider_id=provider,
            name=name,
            access=CapabilityAccess.READ,
            required_scopes=frozenset(scopes),
            verification=VerificationMode.REQUIRED,
            timeout_seconds=30,
            repeatable=repeatable,
            minimum_poll_interval_seconds=300 if repeatable else None,
            maximum_monitor_polls=2016 if repeatable else None,
            monitor_ttl_seconds=30 * 86400 if repeatable else None,
            monitor_value_paths=value_paths,
        )

    def write(
        capability_id: str,
        name: str,
        scopes: Sequence[str],
        *,
        confirmation: ConfirmationMode = ConfirmationMode.REQUIRED,
        risk: RiskLevel = RiskLevel.HIGH,
    ) -> CapabilityMetadata:
        return CapabilityMetadata(
            capability_id=capability_id,
            provider_id=provider,
            name=name,
            access=CapabilityAccess.WRITE,
            required_scopes=frozenset(scopes),
            confirmation=confirmation,
            risk=risk,
            verification=VerificationMode.REQUIRED,
            timeout_seconds=30,
        )

    return (
        read(
            "gmail.search",
            "Search Gmail messages",
            (SCOPE_GMAIL_READ,),
            repeatable=True,
            value_paths=("latest_message_id", "message_ids", "count"),
        ),
        read("gmail.read", "Read Gmail message", (SCOPE_GMAIL_READ,)),
        read(
            "gmail.thread",
            "Read or search Gmail threads",
            (SCOPE_GMAIL_READ,),
            repeatable=True,
            value_paths=("latest_message_id", "thread_ids", "count"),
        ),
        write(
            "gmail.draft",
            "Create or edit Gmail draft",
            (SCOPE_GMAIL_COMPOSE,),
            confirmation=ConfirmationMode.NONE,
            risk=RiskLevel.MEDIUM,
        ),
        write(
            "gmail.reply",
            "Create Gmail reply draft",
            (SCOPE_GMAIL_READ, SCOPE_GMAIL_COMPOSE),
            confirmation=ConfirmationMode.NONE,
            risk=RiskLevel.MEDIUM,
        ),
        write("gmail.send", "Send Gmail draft", (SCOPE_GMAIL_COMPOSE,)),
        write(
            "gmail.forward",
            "Forward Gmail message",
            (SCOPE_GMAIL_READ, SCOPE_GMAIL_COMPOSE),
        ),
        write("gmail.archive", "Archive Gmail message", (SCOPE_GMAIL_MODIFY,)),
        read("calendar.list", "List Google calendars and events", (SCOPE_CALENDAR_READ,)),
        read("calendar.read", "Read Google Calendar event", (SCOPE_CALENDAR_READ,)),
        read("calendar.search", "Search Google Calendar events", (SCOPE_CALENDAR_READ,)),
        read(
            "calendar.availability",
            "Read Google Calendar free/busy",
            (SCOPE_CALENDAR_READ,),
        ),
        read("calendar.timezone", "Read Google Calendar timezone", (SCOPE_CALENDAR_READ,)),
        write("calendar.create", "Create Google Calendar event", (SCOPE_CALENDAR_WRITE,)),
        write("calendar.update", "Update Google Calendar event", (SCOPE_CALENDAR_WRITE,)),
        write("calendar.cancel", "Delete Google Calendar event", (SCOPE_CALENDAR_WRITE,)),
        read("contacts.search", "Search Google contacts", (SCOPE_CONTACTS_READ,)),
        read("contacts.resolve", "Resolve a Google contact", (SCOPE_CONTACTS_READ,)),
    )


class GoogleTokenManager:
    def __init__(
        self,
        *,
        config: GoogleOAuthConfig,
        accounts: IntegrationAccountStore,
        client: httpx.AsyncClient,
    ) -> None:
        self.config = config
        self.accounts = accounts
        self.client = client

    async def access_token(self, principal_id: str, *, force_refresh: bool = False) -> str:
        row = await self.accounts.account(principal_id=principal_id, provider="google")
        if row is None:
            raise GoogleProviderError("Google account is not connected")
        credentials = await self.accounts.account_credentials(row["account_id"])
        token = str(credentials.get("access_token") or "")
        expires = self._expiry(credentials.get("expires_at"))
        if token and not force_refresh and expires > _utc_now() + timedelta(seconds=60):
            return token
        refresh_token = str(credentials.get("refresh_token") or "")
        if not refresh_token:
            await self.accounts.mark_health(
                row["account_id"],
                authenticated=False,
                healthy=False,
                reason="Google reauthorization is required",
                reauthorization_required=True,
            )
            raise GoogleProviderError("Google reauthorization is required", status_code=401)
        try:
            response = await self.client.post(
                GOOGLE_TOKEN_ENDPOINT,
                data={
                    "client_id": self.config.client_id.strip(),
                    "client_secret": self.config.client_secret.strip(),
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                headers={"Accept": "application/json"},
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise GoogleProviderError(
                "Google token refresh transport failed",
                retryable=True,
            ) from exc
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            payload = {}
        if response.status_code != 200 or not isinstance(payload, Mapping):
            reauth = response.status_code in {400, 401, 403}
            await self.accounts.mark_health(
                row["account_id"],
                authenticated=not reauth,
                healthy=False,
                reason=(
                    "Google authorization was revoked or expired"
                    if reauth
                    else "Google token refresh failed"
                ),
                reauthorization_required=reauth,
            )
            raise GoogleProviderError(
                "Google token refresh failed",
                status_code=response.status_code,
                reauthorization_required=reauth,
            )
        access_token = str(payload.get("access_token") or "")
        if not access_token:
            raise GoogleProviderError("Google refresh response was malformed")
        existing_scopes = tuple(str(item) for item in credentials.get("scopes") or ())
        scopes = tuple(str(payload.get("scope") or "").split()) or existing_scopes
        if set(scopes) - set(existing_scopes):
            await self.accounts.mark_health(
                row["account_id"],
                authenticated=True,
                healthy=False,
                reason="Google refresh returned an unexpected scope set",
            )
            raise GoogleProviderError("Google refresh returned an unexpected scope set")
        updated = {
            **credentials,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": str(payload.get("token_type") or "Bearer"),
            "expires_at": _iso(
                _utc_now() + timedelta(seconds=max(1, int(payload.get("expires_in") or 3600)))
            ),
            "scopes": list(scopes),
        }
        await self.accounts.update_credentials(row["account_id"], updated, scopes=scopes)
        return access_token

    @staticmethod
    def _expiry(value: Any) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return datetime.min.replace(tzinfo=timezone.utc)


class GoogleConnector(Connector):
    provider_id = "google"

    def __init__(
        self,
        *,
        oauth: GoogleOAuthService,
        accounts: IntegrationAccountStore,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(
            provider_id=self.provider_id,
            name="Google Account",
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
        self.tokens = GoogleTokenManager(
            config=oauth.config,
            accounts=accounts,
            client=self.client,
        )
        self._service_health: dict[str, dict[str, dict[str, Any]]] = {}
        self._verified_capabilities: dict[str, tuple[str, ...]] = {}

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def status(self) -> ProviderStatus:
        return self._status_unconfigured(
            "Google account status is principal-scoped; connect from Integrations"
        )

    async def status_for_principal(self, principal_id: str | None) -> ProviderStatus:
        principal = str(principal_id or "").strip()
        if not self.oauth.configured:
            return self._status_unconfigured("Google OAuth setup is incomplete")
        if not principal:
            return self._status_unconfigured("An authenticated account owner is required")
        row = await self.accounts.account(principal_id=principal, provider="google")
        if row is None:
            return self._status_unconfigured("Google account is not connected")
        scopes = frozenset(json.loads(row["scopes_json"]))
        if not bool(row["authenticated"]):
            return ProviderStatus(
                provider_id=self.provider_id,
                name=self.name,
                configured=True,
                authenticated=False,
                healthy=False,
                health_reason=row["health_reason"] or "Google reauthorization is required",
                setup_requirements=("Reconnect Google",),
                scopes=scopes,
                potential_capabilities=tuple(item.capability_id for item in self.capabilities),
                executable_capabilities=(),
            )
        try:
            identity = await self._request(principal, "GET", GOOGLE_USERINFO_ENDPOINT)
            if str(identity.get("sub") or "") != row["provider_subject"]:
                raise GoogleProviderError("Google account identity changed")
            service_health, executable = await self._probe_services(principal, scopes)
            self._service_health[principal] = service_health
            self._verified_capabilities[principal] = executable
            healthy = bool(executable)
            failed_services = sorted(
                name
                for name, item in service_health.items()
                if item["granted"] and not item["healthy"]
            )
            reason = (
                "Some Google services are unavailable: " + ", ".join(failed_services)
                if failed_services
                else (None if healthy else "No granted Google capability passed a provider probe")
            )
            await self.accounts.mark_health(
                row["account_id"],
                authenticated=True,
                healthy=healthy,
                reason=reason,
            )
        except GoogleProviderError as exc:
            reauth = exc.reauthorization_required
            await self.accounts.mark_health(
                row["account_id"],
                authenticated=not reauth,
                healthy=False,
                reason=str(exc),
                reauthorization_required=reauth,
            )
            return ProviderStatus(
                provider_id=self.provider_id,
                name=self.name,
                configured=True,
                authenticated=not reauth,
                healthy=False,
                health_reason=redact_text(exc, max_length=500),
                setup_requirements=(("Reconnect Google",) if reauth else ("Retry later",)),
                scopes=scopes,
                potential_capabilities=tuple(item.capability_id for item in self.capabilities),
                executable_capabilities=(),
            )
        return ProviderStatus(
            provider_id=self.provider_id,
            name=self.name,
            configured=True,
            authenticated=True,
            healthy=healthy,
            health_reason=reason,
            scopes=scopes,
            potential_capabilities=tuple(item.capability_id for item in self.capabilities),
            executable_capabilities=executable,
        )

    async def _probe_services(
        self,
        principal: str,
        scopes: frozenset[str],
    ) -> tuple[dict[str, dict[str, Any]], tuple[str, ...]]:
        """Probe each granted Google product before exposing its capabilities."""

        calendar_probe_url = (
            f"{CALENDAR_API}/users/me/calendarList"
            if SCOPE_CALENDAR_READ in scopes
            else f"{CALENDAR_API}/calendars/primary/events"
        )
        calendar_probe_params: Mapping[str, Any] = (
            {"maxResults": 1}
            if SCOPE_CALENDAR_READ in scopes
            else {"maxResults": 1, "singleEvents": "true", "orderBy": "startTime"}
        )
        probes: Mapping[str, tuple[frozenset[str], str, Mapping[str, Any] | None]] = {
            "gmail": (
                frozenset({SCOPE_GMAIL_READ, SCOPE_GMAIL_MODIFY, SCOPE_GMAIL_COMPOSE}),
                (
                    f"{GMAIL_API}/messages"
                    if SCOPE_GMAIL_READ in scopes or SCOPE_GMAIL_MODIFY in scopes
                    else f"{GMAIL_API}/drafts"
                ),
                {"maxResults": 1},
            ),
            "calendar": (
                frozenset({SCOPE_CALENDAR_READ, SCOPE_CALENDAR_WRITE}),
                calendar_probe_url,
                calendar_probe_params,
            ),
            "contacts": (
                frozenset({SCOPE_CONTACTS_READ}),
                f"{PEOPLE_API}/people/me/connections",
                {"personFields": "names", "pageSize": 1},
            ),
        }
        health: dict[str, dict[str, Any]] = {}
        executable: list[str] = []
        checked_at = _iso()
        capabilities_by_service = {
            service: tuple(
                item
                for item in self.capabilities
                if item.capability_id.startswith(service + ".")
                and item.required_scopes.issubset(scopes)
            )
            for service in probes
        }
        for service, (service_scopes, url, params) in probes.items():
            granted = bool(scopes & service_scopes) and bool(capabilities_by_service[service])
            if not granted:
                health[service] = {
                    "granted": False,
                    "healthy": False,
                    "reason": "Permission required",
                    "last_probe_at": None,
                    "last_successful_probe_at": None,
                    "last_error_category": "permission_required",
                }
                continue
            try:
                await self._request(principal, "GET", url, params=params)
            except GoogleProviderError as exc:
                if exc.reauthorization_required:
                    raise
                health[service] = {
                    "granted": True,
                    "healthy": False,
                    "reason": redact_text(exc, max_length=300),
                    "last_probe_at": checked_at,
                    "last_successful_probe_at": None,
                    "last_error_category": self._provider_error_category(exc),
                }
                continue
            health[service] = {
                "granted": True,
                "healthy": True,
                "reason": None,
                "last_probe_at": checked_at,
                "last_successful_probe_at": checked_at,
                "last_error_category": None,
            }
            executable.extend(item.capability_id for item in capabilities_by_service[service])
        return health, tuple(dict.fromkeys(executable))

    @staticmethod
    def _provider_error_category(error: GoogleProviderError) -> str:
        if error.reauthorization_required:
            return "reauthentication_required"
        if error.status_code == 403:
            return "permission_or_policy_rejected"
        if error.status_code == 429:
            return "rate_limited"
        if error.status_code is not None and error.status_code >= 500:
            return "provider_unavailable"
        if error.retryable:
            return "transport_unavailable"
        return "malformed_or_rejected_response"

    def service_health(self, principal_id: str) -> dict[str, dict[str, Any]]:
        return {
            key: dict(value)
            for key, value in self._service_health.get(str(principal_id).strip(), {}).items()
        }

    async def credential_status(self, principal_id: str) -> dict[str, Any] | None:
        row = await self.accounts.account(principal_id=principal_id, provider="google")
        if row is None:
            return None
        try:
            return await self.accounts.credential_status(str(row["account_id"]))
        except (CredentialEncryptionUnavailable, ValueError):
            # This endpoint is diagnostic only. Fail closed without leaking the
            # credential envelope, key state, or provider token material.
            return {
                "access_token_present": False,
                "refresh_token_present": False,
                "expires_at": None,
                "expired": False,
                "expires_soon": False,
                "error_category": "credential_unavailable",
            }

    def _status_unconfigured(self, reason: str) -> ProviderStatus:
        requirements = self.oauth.setup_requirements or ("Connect Google",)
        return ProviderStatus(
            provider_id=self.provider_id,
            name=self.name,
            configured=False,
            authenticated=False,
            healthy=False,
            health_reason=reason,
            setup_requirements=requirements,
            potential_capabilities=tuple(item.capability_id for item in self.capabilities),
            executable_capabilities=(),
        )

    async def account_status(self, principal_id: str) -> IntegrationAccount | None:
        row = await self.accounts.account(principal_id=principal_id, provider="google")
        if row is None:
            return None
        scopes = frozenset(json.loads(row["scopes_json"]))
        available = (
            self._verified_capabilities.get(str(principal_id).strip(), ())
            if bool(row["authenticated"]) and bool(row["healthy"])
            else ()
        )
        by_id = {item.capability_id: item for item in self.capabilities}
        return IntegrationAccount(
            provider="google",
            account_id=row["account_id"],
            principal_id=row["principal_id"],
            provider_subject=row["provider_subject"],
            account_display_name=row["display_name"],
            account_email=row["email"],
            configured=True,
            authenticated=bool(row["authenticated"]),
            healthy=bool(row["healthy"]),
            granted_scopes=tuple(sorted(scopes)),
            available_capabilities=available,
            read_capabilities=tuple(
                item for item in available if by_id[item].access is CapabilityAccess.READ
            ),
            write_capabilities=tuple(
                item for item in available if by_id[item].access is CapabilityAccess.WRITE
            ),
            last_health_check=row["last_health_check"],
            reauthorization_required=bool(row["reauthorization_required"]),
            setup_requirements=(
                ("Reconnect Google",) if bool(row["reauthorization_required"]) else ()
            ),
            health_reason=row["health_reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def disconnect(self, *, principal_id: str, account_id: str) -> dict[str, Any]:
        row = await self.accounts.account(
            principal_id=principal_id,
            provider="google",
            account_id=account_id,
        )
        if row is None:
            return {
                "disconnected": False,
                "revocation_confirmed": False,
                "revocation_status": "not_attempted",
            }
        credentials = await self.accounts.account_credentials(account_id)
        token = str(credentials.get("refresh_token") or credentials.get("access_token") or "")
        revocation_confirmed = False
        revocation_status = "not_applicable"
        if token:
            try:
                response = await self.client.post(
                    GOOGLE_REVOCATION_ENDPOINT,
                    data={"token": token},
                    headers={"Accept": "application/json"},
                )
                revocation_confirmed = response.status_code in {200, 400}
                revocation_status = "confirmed" if revocation_confirmed else "provider_rejected"
            except httpx.HTTPError:
                revocation_status = "provider_unavailable"
        disconnected = await self.accounts.delete_account(
            principal_id=principal_id,
            account_id=account_id,
        )
        return {
            "disconnected": disconnected,
            "revocation_confirmed": revocation_confirmed,
            "revocation_status": revocation_status,
        }

    async def execute(
        self,
        capability: CapabilityMetadata,
        request: CapabilityRequest,
    ) -> ConnectorResult:
        principal = str(request.principal_id or "").strip()
        if not principal:
            return ConnectorResult.failed("An authenticated account owner is required")
        handlers = {
            "gmail.search": self._gmail_search,
            "gmail.read": self._gmail_read,
            "gmail.thread": self._gmail_thread,
            "gmail.draft": self._gmail_draft,
            "gmail.reply": self._gmail_reply,
            "gmail.send": self._gmail_send,
            "gmail.forward": self._gmail_forward,
            "gmail.archive": self._gmail_archive,
            "calendar.list": self._calendar_list,
            "calendar.read": self._calendar_read,
            "calendar.search": self._calendar_search,
            "calendar.availability": self._calendar_availability,
            "calendar.timezone": self._calendar_timezone,
            "calendar.create": self._calendar_create,
            "calendar.update": self._calendar_update,
            "calendar.cancel": self._calendar_cancel,
            "contacts.search": self._contacts_search,
            "contacts.resolve": self._contacts_resolve,
        }
        handler = handlers.get(capability.capability_id)
        if handler is None:
            return ConnectorResult.failed("Unsupported Google capability")
        try:
            payload = dict(request.payload)
            payload["_jarvis_idempotency_key"] = request.idempotency_key or request.request_id
            data, reference = await handler(principal, payload)
            return ConnectorResult.succeeded(data, provider_reference=reference)
        except GoogleProviderError as exc:
            safe = redact_text(exc, max_length=800)
            if capability.access is CapabilityAccess.WRITE and exc.outcome_unknown:
                return ConnectorResult.outcome_unknown(safe)
            return ConnectorResult.failed(safe, retryable=exc.retryable)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            safe = "Google provider transport failed: " + redact_text(exc, max_length=500)
            if capability.access is CapabilityAccess.WRITE:
                return ConnectorResult.outcome_unknown(safe)
            return ConnectorResult.failed(safe, retryable=True)
        except (ValueError, KeyError) as exc:
            return ConnectorResult.failed(redact_text(exc, max_length=800))

    async def verify(
        self,
        capability: CapabilityMetadata,
        request: CapabilityRequest,
        result: ConnectorResult,
    ) -> VerificationResult:
        principal = str(request.principal_id or "").strip()
        reference = str(result.provider_reference or "").strip()
        if not principal or not reference:
            return VerificationResult.unverified("Provider reference is missing")
        try:
            if capability.capability_id in {"gmail.draft", "gmail.reply"}:
                payload = await self._request(
                    principal,
                    "GET",
                    f"{GMAIL_API}/drafts/{reference}",
                    params={"format": "full"},
                )
                if str(payload.get("id") or "") != reference:
                    return VerificationResult.unverified("Draft verification mismatch")
                raw_message = payload.get("message")
                message = raw_message if isinstance(raw_message, Mapping) else {}
                raw_payload = message.get("payload")
                message_payload = raw_payload if isinstance(raw_payload, Mapping) else {}
                summary = self._message_summary(message)
                body = self._body_text(message_payload)
                expected_body = str(request.payload.get("body") or "")
                if body.replace("\r\n", "\n").rstrip("\n") != expected_body.replace(
                    "\r\n", "\n"
                ).rstrip("\n"):
                    return VerificationResult.unverified("Draft body verification mismatch")
                if capability.capability_id == "gmail.draft":
                    try:
                        expected_recipient = self._recipient(str(request.payload.get("to") or ""))
                        observed_recipient = self._recipient(str(summary.get("to") or ""))
                        expected_subject = self._safe_subject(
                            str(request.payload.get("subject") or "")
                        )
                    except ValueError as exc:
                        return VerificationResult.unverified(str(exc))
                    if observed_recipient.casefold() != expected_recipient.casefold():
                        return VerificationResult.unverified(
                            "Draft recipient verification mismatch"
                        )
                    if str(summary.get("subject") or "") != expected_subject:
                        return VerificationResult.unverified("Draft subject verification mismatch")
                elif str(message.get("threadId") or "") != str(result.data.get("thread_id") or ""):
                    return VerificationResult.unverified("Reply thread verification mismatch")
            elif capability.capability_id in {"gmail.send", "gmail.forward"}:
                payload = await self._request(
                    principal,
                    "GET",
                    f"{GMAIL_API}/messages/{reference}",
                    params={
                        "format": "metadata",
                        "metadataHeaders": ["To", "Subject"],
                    },
                )
                if str(payload.get("id") or "") != reference:
                    return VerificationResult.unverified("Sent message verification mismatch")
                if "SENT" not in (payload.get("labelIds") or ()):
                    return VerificationResult.unverified(
                        "Gmail did not verify the message in Sent mail"
                    )
                summary = self._message_summary(payload)
                try:
                    observed_recipient = self._recipient(str(summary.get("to") or ""))
                    expected_recipient = self._recipient(str(result.data.get("recipient") or ""))
                except ValueError as exc:
                    return VerificationResult.unverified(str(exc))
                if observed_recipient.casefold() != expected_recipient.casefold():
                    return VerificationResult.unverified("Sent recipient verification mismatch")
                if str(summary.get("subject") or "") != str(result.data.get("subject") or ""):
                    return VerificationResult.unverified("Sent subject verification mismatch")
            elif capability.capability_id == "gmail.archive":
                payload = await self._request(
                    principal,
                    "GET",
                    f"{GMAIL_API}/messages/{reference}",
                    params={"format": "metadata"},
                )
                if "INBOX" in (payload.get("labelIds") or []):
                    return VerificationResult.unverified("Message still has the INBOX label")
            elif capability.capability_id in {"calendar.create", "calendar.update"}:
                calendar_id = str(result.data.get("calendar_id") or "primary")
                payload = await self._request(
                    principal,
                    "GET",
                    f"{CALENDAR_API}/calendars/{self._segment(calendar_id)}/events/{self._segment(reference)}",
                )
                if str(payload.get("id") or "") != reference:
                    return VerificationResult.unverified("Calendar event verification mismatch")
                expected = (
                    self._event_body(request.payload)
                    if capability.capability_id == "calendar.create"
                    else request.payload.get("changes")
                )
                if not isinstance(expected, Mapping) or not self._calendar_fields_match(
                    payload, expected
                ):
                    return VerificationResult.unverified(
                        "Calendar event fields did not match the requested write"
                    )
            elif capability.capability_id == "calendar.cancel":
                calendar_id = str(result.data.get("calendar_id") or "primary")
                response = await self._raw_request(
                    principal,
                    "GET",
                    f"{CALENDAR_API}/calendars/{self._segment(calendar_id)}/events/{self._segment(reference)}",
                )
                if response.status_code == 200:
                    try:
                        tombstone = response.json()
                    except (json.JSONDecodeError, ValueError):
                        tombstone = None
                    if (
                        not isinstance(tombstone, Mapping)
                        or str(tombstone.get("status") or "").casefold() != "cancelled"
                    ):
                        return VerificationResult.unverified("Calendar event still exists")
                elif response.status_code not in {404, 410}:
                    return VerificationResult.unverified(
                        f"Calendar cancellation readback failed with HTTP {response.status_code}"
                    )
            else:
                return VerificationResult.unverified("Capability has no write verification")
        except GoogleProviderError as exc:
            return VerificationResult.unverified(str(exc))
        return VerificationResult.verified(
            {
                "provider": "google",
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
        token = await self.tokens.access_token(principal)
        try:
            response = await self.client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise GoogleProviderError(
                "Google provider transport failed",
                outcome_unknown=method.upper() not in {"GET", "HEAD", "OPTIONS"},
                retryable=True,
            ) from exc
        if response.status_code == 401:
            token = await self.tokens.access_token(principal, force_refresh=True)
            try:
                response = await self.client.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise GoogleProviderError(
                    "Google provider transport failed after token refresh",
                    outcome_unknown=method.upper() not in {"GET", "HEAD", "OPTIONS"},
                    retryable=True,
                ) from exc
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
            raise GoogleProviderError(
                f"Google API request failed with HTTP {response.status_code}",
                status_code=response.status_code,
            )
        if response.status_code == 204 or not response.content:
            return {}
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise GoogleProviderError("Google API returned malformed JSON") from exc
        if not isinstance(payload, dict):
            raise GoogleProviderError("Google API returned a malformed response")
        return payload

    @staticmethod
    def _segment(value: str) -> str:
        return quote(str(value), safe="")

    @classmethod
    def _contains_expected(cls, actual: Any, expected: Any) -> bool:
        """Match requested provider fields while allowing provider-added metadata."""

        if isinstance(expected, Mapping):
            return isinstance(actual, Mapping) and all(
                key in actual and cls._contains_expected(actual[key], value)
                for key, value in expected.items()
            )
        if isinstance(expected, Sequence) and not isinstance(expected, (str, bytes)):
            return (
                isinstance(actual, Sequence)
                and not isinstance(actual, (str, bytes))
                and len(actual) == len(expected)
                and all(
                    cls._contains_expected(actual_item, expected_item)
                    for actual_item, expected_item in zip(actual, expected, strict=True)
                )
            )
        return actual == expected

    @classmethod
    def _calendar_fields_match(
        cls,
        actual: Mapping[str, Any],
        expected: Mapping[str, Any],
    ) -> bool:
        """Compare event fields while accepting equivalent RFC 3339 timestamps."""

        for key, value in expected.items():
            if key not in actual:
                return False
            if key in {"start", "end"}:
                if not cls._calendar_boundary_matches(actual[key], value):
                    return False
            elif not cls._contains_expected(actual[key], value):
                return False
        return True

    @staticmethod
    def _calendar_boundary_matches(actual: Any, expected: Any) -> bool:
        if not isinstance(actual, Mapping) or not isinstance(expected, Mapping):
            return False
        if expected.get("date") is not None:
            return str(actual.get("date") or "") == str(expected.get("date") or "")
        expected_value = str(expected.get("dateTime") or "").strip()
        actual_value = str(actual.get("dateTime") or "").strip()
        if not expected_value or not actual_value:
            return False

        def parse(value: str) -> datetime | None:
            rendered = value[:-1] + "+00:00" if value.endswith("Z") else value
            try:
                parsed = datetime.fromisoformat(rendered)
            except ValueError:
                return None
            return parsed if parsed.tzinfo is not None else None

        expected_time = parse(expected_value)
        actual_time = parse(actual_value)
        if expected_time is None or actual_time is None:
            return actual_value == expected_value
        return actual_time.astimezone(timezone.utc) == expected_time.astimezone(timezone.utc)

    @staticmethod
    def _required(payload: Mapping[str, Any], key: str, *, max_length: int = 10_000) -> str:
        value = str(payload.get(key) or "").strip()
        if not value:
            raise ValueError(f"{key} is required")
        if len(value) > max_length:
            raise ValueError(f"{key} is too long")
        return value

    @staticmethod
    def _limit(payload: Mapping[str, Any], default: int = 20) -> int:
        return max(1, min(int(payload.get("limit") or default), 100))

    @staticmethod
    def _message_summary(message: Mapping[str, Any]) -> dict[str, Any]:
        raw_payload = message.get("payload")
        payload: Mapping[str, Any] = raw_payload if isinstance(raw_payload, Mapping) else {}
        headers = {
            str(item.get("name") or "").casefold(): str(item.get("value") or "")
            for item in payload.get("headers") or ()
            if isinstance(item, Mapping)
        }
        return {
            "message_id": message.get("id"),
            "thread_id": message.get("threadId"),
            "label_ids": list(message.get("labelIds") or ()),
            "snippet": str(message.get("snippet") or "")[:2_000],
            "from": headers.get("from"),
            "to": headers.get("to"),
            "subject": headers.get("subject"),
            "date": headers.get("date"),
            "message_id_header": headers.get("message-id"),
            "reply_to": headers.get("reply-to"),
            "attachments": GoogleConnector._attachment_metadata(payload),
        }

    @staticmethod
    def _attachment_metadata(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        attachments: list[dict[str, Any]] = []

        def visit(part: Mapping[str, Any]) -> None:
            filename = str(part.get("filename") or "").strip()
            raw_body = part.get("body")
            body = raw_body if isinstance(raw_body, Mapping) else {}
            attachment_id = str(body.get("attachmentId") or "").strip()
            if filename or attachment_id:
                attachments.append(
                    {
                        "filename": filename or None,
                        "mime_type": str(part.get("mimeType") or "") or None,
                        "size": max(0, int(body.get("size") or 0)),
                        "attachment_id": attachment_id or None,
                    }
                )
            for child in part.get("parts") or ():
                if isinstance(child, Mapping) and len(attachments) < 100:
                    visit(child)

        visit(payload)
        return attachments[:100]

    @staticmethod
    def _recipient(value: str) -> str:
        """Return one syntactically safe address, discarding untrusted display text."""

        raw = str(value or "").strip()
        if not raw or len(raw) > 320 or "\r" in raw or "\n" in raw:
            raise ValueError("A single valid recipient email address is required")
        parsed = getaddresses([raw])
        if len(parsed) != 1:
            raise ValueError("A single valid recipient email address is required")
        address = parsed[0][1].strip()
        local, separator, domain = address.rpartition("@")
        if (
            not separator
            or not local
            or not domain
            or len(address) > 320
            or any(character.isspace() or ord(character) < 32 for character in address)
            or domain.startswith(".")
            or domain.endswith(".")
        ):
            raise ValueError("A single valid recipient email address is required")
        return address

    @staticmethod
    def _safe_subject(value: str) -> str:
        subject = str(value or "").strip()
        if not subject or len(subject) > 1_000 or "\r" in subject or "\n" in subject:
            raise ValueError("subject must be a single non-empty header line")
        return subject

    @classmethod
    def _body_text(cls, payload: Mapping[str, Any]) -> str:
        mime = str(payload.get("mimeType") or "")
        raw_body = payload.get("body")
        body: Mapping[str, Any] = raw_body if isinstance(raw_body, Mapping) else {}
        data = str(body.get("data") or "")
        if data and mime in {"text/plain", "text/html", ""}:
            try:
                return _decode_b64url(data).decode("utf-8", errors="replace")[:100_000]
            except (ValueError, UnicodeError):
                return ""
        for part in payload.get("parts") or ():
            if isinstance(part, Mapping):
                text = cls._body_text(part)
                if text:
                    return text
        return ""

    async def _gmail_search(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        query = str(payload.get("query") or "in:inbox").strip()
        if len(query) > 1_000:
            raise ValueError("query is too long")
        result = await self._request(
            principal,
            "GET",
            f"{GMAIL_API}/messages",
            params={"q": query, "maxResults": self._limit(payload)},
        )
        messages = [item for item in result.get("messages") or () if isinstance(item, Mapping)]
        ids = [str(item.get("id")) for item in messages if item.get("id")]
        details = await asyncio.gather(
            *(
                self._request(
                    principal,
                    "GET",
                    f"{GMAIL_API}/messages/{self._segment(message_id)}",
                    params={"format": "metadata"},
                )
                for message_id in ids[:25]
            )
        )
        summaries = [self._message_summary(item) for item in details]
        return {
            "query": query,
            "count": len(ids),
            "message_ids": ids,
            "messages": summaries,
            "latest_message_id": ids[0] if ids else None,
            "result_size_estimate": int(result.get("resultSizeEstimate") or len(ids)),
        }, ids[0] if ids else None

    async def _gmail_read(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        message_id = self._required(payload, "message_id", max_length=300)
        message = await self._request(
            principal,
            "GET",
            f"{GMAIL_API}/messages/{self._segment(message_id)}",
            params={"format": "full"},
        )
        summary = self._message_summary(message)
        raw_payload = message.get("payload")
        summary["body"] = self._body_text(raw_payload) if isinstance(raw_payload, Mapping) else ""
        return summary, message_id

    async def _gmail_thread(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        thread_id = str(payload.get("thread_id") or "").strip()
        if not thread_id:
            query = self._required(payload, "query", max_length=1_000)
            result = await self._request(
                principal,
                "GET",
                f"{GMAIL_API}/threads",
                params={"q": query, "maxResults": self._limit(payload)},
            )
            threads = [item for item in result.get("threads") or () if isinstance(item, Mapping)]
            ids = [str(item.get("id")) for item in threads if item.get("id")]
            details = await asyncio.gather(
                *(
                    self._request(
                        principal,
                        "GET",
                        f"{GMAIL_API}/threads/{self._segment(item)}",
                        params={"format": "full"},
                    )
                    for item in ids[:25]
                )
            )
            summaries = [self._thread_summary(item) for item in details]
            return {
                "query": query,
                "count": len(ids),
                "thread_ids": ids,
                "threads": summaries,
                "latest_message_id": (summaries[0].get("latest_message_id") if summaries else None),
            }, ids[0] if ids else None
        thread = await self._request(
            principal,
            "GET",
            f"{GMAIL_API}/threads/{self._segment(thread_id)}",
            params={"format": "full"},
        )
        return self._thread_summary(thread, fallback_id=thread_id), thread_id

    @classmethod
    def _thread_summary(
        cls, thread: Mapping[str, Any], *, fallback_id: str | None = None
    ) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        remaining_body_characters = 200_000
        for item in thread.get("messages") or ():
            if not isinstance(item, Mapping) or len(messages) >= 100:
                continue
            summary = cls._message_summary(item)
            raw_payload = item.get("payload")
            body = cls._body_text(raw_payload) if isinstance(raw_payload, Mapping) else ""
            summary["body"] = (
                body[:remaining_body_characters] if remaining_body_characters > 0 else ""
            )
            remaining_body_characters -= len(summary["body"])
            messages.append(summary)
        return {
            "thread_id": str(thread.get("id") or fallback_id or ""),
            "count": len(messages),
            "messages": messages,
            "latest_message_id": messages[-1].get("message_id") if messages else None,
        }

    @staticmethod
    def _mime(
        *,
        to: str,
        subject: str,
        body: str,
        from_email: str | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
    ) -> str:
        message = EmailMessage()
        message["To"] = to
        if from_email:
            message["From"] = from_email
        message["Subject"] = subject
        message["Date"] = formatdate(localtime=False)
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
        if references:
            message["References"] = references
        message.set_content(body)
        return _b64url(message.as_bytes())

    async def _gmail_draft(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        draft_id = str(payload.get("draft_id") or "").strip()
        raw = self._mime(
            to=self._recipient(self._required(payload, "to", max_length=320)),
            subject=self._safe_subject(self._required(payload, "subject", max_length=1_000)),
            body=self._required(payload, "body", max_length=100_000),
        )
        body: dict[str, Any] = {"message": {"raw": raw}}
        if draft_id:
            result = await self._request(
                principal,
                "PUT",
                f"{GMAIL_API}/drafts/{self._segment(draft_id)}",
                json_body={"id": draft_id, **body},
            )
        else:
            result = await self._request(principal, "POST", f"{GMAIL_API}/drafts", json_body=body)
        reference = str(result.get("id") or "")
        if not reference:
            raise GoogleProviderError("Gmail did not return a draft ID")
        return {
            "draft_id": reference,
            "message_id": (result.get("message") or {}).get("id")
            if isinstance(result.get("message"), Mapping)
            else None,
            "status": "drafted",
        }, reference

    async def _gmail_reply(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        message_id = self._required(payload, "message_id", max_length=300)
        original, _ = await self._gmail_read(principal, {"message_id": message_id})
        sender = self._recipient(str(original.get("reply_to") or original.get("from") or ""))
        subject = str(original.get("subject") or "").strip() or "(no subject)"
        if not sender:
            raise GoogleProviderError("Original message has no verified sender header")
        if not subject.casefold().startswith("re:"):
            subject = "Re: " + subject
        raw = self._mime(
            to=sender,
            subject=subject,
            body=self._required(payload, "body", max_length=100_000),
            in_reply_to=str(original.get("message_id_header") or "") or None,
            references=str(original.get("message_id_header") or "") or None,
        )
        result = await self._request(
            principal,
            "POST",
            f"{GMAIL_API}/drafts",
            json_body={"message": {"raw": raw, "threadId": original.get("thread_id")}},
        )
        reference = str(result.get("id") or "")
        if not reference:
            raise GoogleProviderError("Gmail did not return a reply draft ID")
        return {
            "draft_id": reference,
            "thread_id": original.get("thread_id"),
            "status": "drafted",
        }, reference

    async def _gmail_send(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        draft_id = self._required(payload, "draft_id", max_length=300)
        draft = await self._request(
            principal,
            "GET",
            f"{GMAIL_API}/drafts/{self._segment(draft_id)}",
            params={"format": "full"},
        )
        raw_message = draft.get("message")
        message = raw_message if isinstance(raw_message, Mapping) else {}
        raw_payload = message.get("payload")
        message_payload = raw_payload if isinstance(raw_payload, Mapping) else {}
        summary = self._message_summary(message)
        recipient = self._recipient(str(summary.get("to") or ""))
        subject = self._safe_subject(str(summary.get("subject") or ""))
        body_digest = hashlib.sha256(self._body_text(message_payload).encode("utf-8")).hexdigest()
        result = await self._request(
            principal,
            "POST",
            f"{GMAIL_API}/drafts/send",
            json_body={"id": draft_id},
        )
        reference = str(result.get("id") or "")
        if not reference:
            raise GoogleProviderError("Gmail did not return a sent message ID")
        return {
            "message_id": reference,
            "thread_id": result.get("threadId"),
            "recipient": recipient,
            "subject": subject,
            "body_sha256": body_digest,
            "status": "sent",
        }, reference

    async def _gmail_forward(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        original, _ = await self._gmail_read(
            principal,
            {"message_id": self._required(payload, "message_id", max_length=300)},
        )
        subject = str(original.get("subject") or "")
        if not subject.casefold().startswith("fwd:"):
            subject = "Fwd: " + subject
        body = self._required(payload, "body", max_length=100_000)
        quoted = str(original.get("body") or "")[:50_000]
        raw = self._mime(
            to=self._recipient(self._required(payload, "to", max_length=320)),
            subject=self._safe_subject(subject),
            body=body + "\n\n---------- Forwarded message ----------\n" + quoted,
        )
        result = await self._request(
            principal,
            "POST",
            f"{GMAIL_API}/messages/send",
            json_body={"raw": raw},
        )
        reference = str(result.get("id") or "")
        if not reference:
            raise GoogleProviderError("Gmail did not return a sent message ID")
        return {
            "message_id": reference,
            "recipient": self._recipient(self._required(payload, "to", max_length=320)),
            "subject": self._safe_subject(subject),
            "status": "sent",
        }, reference

    async def _gmail_archive(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        message_id = self._required(payload, "message_id", max_length=300)
        result = await self._request(
            principal,
            "POST",
            f"{GMAIL_API}/messages/{self._segment(message_id)}/modify",
            json_body={"removeLabelIds": ["INBOX"]},
        )
        return {
            "message_id": message_id,
            "label_ids": list(result.get("labelIds") or ()),
            "status": "archived",
        }, message_id

    async def _calendar_list(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        if payload.get("events") is True:
            calendar_id = str(payload.get("calendar_id") or "primary")
            params = {
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": self._limit(payload),
            }
            for key in ("timeMin", "timeMax"):
                if payload.get(key):
                    params[key] = str(payload[key])
            result = await self._request(
                principal,
                "GET",
                f"{CALENDAR_API}/calendars/{self._segment(calendar_id)}/events",
                params=params,
            )
            events = [
                self._event(item) for item in result.get("items") or () if isinstance(item, Mapping)
            ]
            return {"calendar_id": calendar_id, "events": events, "count": len(events)}, None
        result = await self._request(
            principal,
            "GET",
            f"{CALENDAR_API}/users/me/calendarList",
            params={"maxResults": self._limit(payload, 100)},
        )
        calendars = [
            {
                "calendar_id": item.get("id"),
                "summary": item.get("summary"),
                "primary": bool(item.get("primary")),
                "access_role": item.get("accessRole"),
                "time_zone": item.get("timeZone"),
            }
            for item in result.get("items") or ()
            if isinstance(item, Mapping)
        ]
        return {"calendars": calendars, "count": len(calendars)}, None

    async def _calendar_search(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        calendar_id = str(payload.get("calendar_id") or "primary")
        params: dict[str, Any] = {
            "q": self._required(payload, "query", max_length=1_000),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": self._limit(payload),
        }
        for key in ("timeMin", "timeMax"):
            if payload.get(key):
                params[key] = str(payload[key])
        result = await self._request(
            principal,
            "GET",
            f"{CALENDAR_API}/calendars/{self._segment(calendar_id)}/events",
            params=params,
        )
        events = [
            self._event(item) for item in result.get("items") or () if isinstance(item, Mapping)
        ]
        return {
            "calendar_id": calendar_id,
            "events": events,
            "count": len(events),
            "resolved": len(events) == 1,
            "ambiguous": len(events) > 1,
        }, (str(events[0].get("event_id") or "") if len(events) == 1 else None)

    async def _calendar_read(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        calendar_id = str(payload.get("calendar_id") or "primary")
        event_id = self._required(payload, "event_id", max_length=500)
        result = await self._request(
            principal,
            "GET",
            f"{CALENDAR_API}/calendars/{self._segment(calendar_id)}/events/{self._segment(event_id)}",
        )
        return {"calendar_id": calendar_id, "event": self._event(result)}, event_id

    async def _calendar_availability(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        calendars = payload.get("calendar_ids") or ["primary"]
        if not isinstance(calendars, Sequence) or isinstance(calendars, (str, bytes)):
            raise ValueError("calendar_ids must be an array")
        result = await self._request(
            principal,
            "POST",
            f"{CALENDAR_API}/freeBusy",
            json_body={
                "timeMin": self._required(payload, "timeMin", max_length=100),
                "timeMax": self._required(payload, "timeMax", max_length=100),
                "timeZone": str(payload.get("timeZone") or "UTC"),
                "items": [{"id": str(item)} for item in list(calendars)[:20]],
            },
        )
        return {
            "calendars": result.get("calendars") or {},
            "time_zone": payload.get("timeZone") or "UTC",
        }, None

    async def _calendar_timezone(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        result = await self._request(
            principal,
            "GET",
            f"{CALENDAR_API}/users/me/settings/timezone",
        )
        return {"time_zone": result.get("value")}, None

    @staticmethod
    def _event(item: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "event_id": item.get("id"),
            "status": item.get("status"),
            "summary": item.get("summary"),
            "description": item.get("description"),
            "location": item.get("location"),
            "start": item.get("start"),
            "end": item.get("end"),
            "html_link": item.get("htmlLink"),
            "updated": item.get("updated"),
        }

    @staticmethod
    def _event_body(payload: Mapping[str, Any]) -> dict[str, Any]:
        event: dict[str, Any] = {
            "summary": GoogleConnector._required(payload, "summary", max_length=1_000),
            "start": payload.get("start"),
            "end": payload.get("end"),
        }
        if not isinstance(event["start"], Mapping) or not isinstance(event["end"], Mapping):
            raise ValueError("start and end must be structured calendar date/time objects")
        for boundary in ("start", "end"):
            rendered = event[boundary]
            assert isinstance(rendered, Mapping)
            if not isinstance(rendered.get("dateTime") or rendered.get("date"), str):
                raise ValueError(f"{boundary} must contain dateTime or date")
        for key in ("description", "location"):
            if payload.get(key) is not None:
                event[key] = GoogleConnector._required(payload, key, max_length=10_000)
        attendees = payload.get("attendees")
        if attendees is not None:
            if not isinstance(attendees, Sequence) or isinstance(attendees, (str, bytes)):
                raise ValueError("attendees must be an array")
            if len(attendees) > 100:
                raise ValueError("attendees exceeds the supported limit")
            verified_attendees: list[dict[str, str]] = []
            for attendee in attendees:
                if not isinstance(attendee, Mapping):
                    raise ValueError("Each attendee must be an object")
                verified_attendees.append(
                    {"email": GoogleConnector._recipient(str(attendee.get("email") or ""))}
                )
            event["attendees"] = verified_attendees
        return event

    async def _calendar_create(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        calendar_id = str(payload.get("calendar_id") or "primary")
        event_body = self._event_body(payload)
        idempotency_key = str(payload.get("_jarvis_idempotency_key") or "").strip()
        deterministic_event_id = ""
        if idempotency_key:
            deterministic_event_id = (
                "j" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:40]
            )
            event_body["id"] = deterministic_event_id
        try:
            result = await self._request(
                principal,
                "POST",
                f"{CALENDAR_API}/calendars/{self._segment(calendar_id)}/events",
                json_body=event_body,
            )
        except GoogleProviderError as exc:
            if exc.status_code != 409 or not deterministic_event_id:
                raise
            result = await self._request(
                principal,
                "GET",
                f"{CALENDAR_API}/calendars/{self._segment(calendar_id)}/events/"
                f"{self._segment(deterministic_event_id)}",
            )
            expected = {key: value for key, value in event_body.items() if key != "id"}
            if not self._contains_expected(result, expected):
                raise GoogleProviderError(
                    "Existing Google Calendar event did not match the idempotent request"
                ) from exc
        reference = str(result.get("id") or "")
        if not reference:
            raise GoogleProviderError("Google Calendar did not return an event ID")
        return {
            "calendar_id": calendar_id,
            "event": self._event(result),
            "status": "created",
        }, reference

    async def _calendar_update(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        calendar_id = str(payload.get("calendar_id") or "primary")
        event_id = self._required(payload, "event_id", max_length=500)
        changes = payload.get("changes")
        if not isinstance(changes, Mapping) or not changes:
            raise ValueError("changes must be a non-empty object")
        allowed = {"summary", "description", "location", "start", "end", "attendees"}
        if set(changes) - allowed:
            raise ValueError("Calendar update contains unsupported fields")
        result = await self._request(
            principal,
            "PATCH",
            f"{CALENDAR_API}/calendars/{self._segment(calendar_id)}/events/{self._segment(event_id)}",
            json_body=dict(changes),
        )
        return {
            "calendar_id": calendar_id,
            "event": self._event(result),
            "status": "updated",
        }, event_id

    async def _calendar_cancel(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        calendar_id = str(payload.get("calendar_id") or "primary")
        event_id = self._required(payload, "event_id", max_length=500)
        response = await self._raw_request(
            principal,
            "DELETE",
            f"{CALENDAR_API}/calendars/{self._segment(calendar_id)}/events/{self._segment(event_id)}",
        )
        if response.status_code not in {204, 404, 410}:
            raise GoogleProviderError(
                f"Google API request failed with HTTP {response.status_code}",
                status_code=response.status_code,
            )
        return {
            "calendar_id": calendar_id,
            "event_id": event_id,
            "status": "deleted",
            "already_absent": response.status_code in {404, 410},
        }, event_id

    async def _contacts_search(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        query = self._required(payload, "query", max_length=500)
        result = await self._request(
            principal,
            "GET",
            f"{PEOPLE_API}/people:searchContacts",
            params={
                "query": query,
                "readMask": "names,emailAddresses,phoneNumbers,organizations,metadata",
                "pageSize": min(self._limit(payload), 30),
            },
        )
        raw_contacts = [
            self._contact(item.get("person") or {})
            for item in result.get("results") or ()
            if isinstance(item, Mapping) and isinstance(item.get("person"), Mapping)
        ]
        contacts: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for contact in raw_contacts:
            identity = (
                str(contact.get("resource_name") or ""),
                tuple(str(item).casefold() for item in contact["email_addresses"]),
                tuple(str(item) for item in contact["phone_numbers"]),
            )
            if identity in seen:
                continue
            seen.add(identity)
            contacts.append(contact)
        return {"query": query, "contacts": contacts, "count": len(contacts)}, None

    async def _contacts_resolve(
        self, principal: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        search, _ = await self._contacts_search(principal, payload)
        contacts = search["contacts"]
        if not contacts:
            return {"resolved": False, "ambiguous": False, "matches": []}, None
        if len(contacts) > 1:
            return {
                "resolved": False,
                "ambiguous": True,
                "matches": contacts,
                "error": "Multiple contacts match; clarification is required",
            }, None
        contact = contacts[0]
        return {"resolved": True, "ambiguous": False, "contact": contact}, str(
            contact.get("resource_name") or ""
        ) or None

    @staticmethod
    def _contact(person: Mapping[str, Any]) -> dict[str, Any]:
        names = [item for item in person.get("names") or () if isinstance(item, Mapping)]
        organizations = [
            item for item in person.get("organizations") or () if isinstance(item, Mapping)
        ]
        emails = [
            str(item.get("value")).strip()
            for item in person.get("emailAddresses") or ()
            if isinstance(item, Mapping) and item.get("value")
        ]
        phones = [
            str(item.get("value")).strip()
            for item in person.get("phoneNumbers") or ()
            if isinstance(item, Mapping) and item.get("value")
        ]
        return {
            "resource_name": person.get("resourceName"),
            "display_name": names[0].get("displayName") if names else None,
            "email_addresses": list(dict.fromkeys(item for item in emails if item))[:100],
            "phone_numbers": list(dict.fromkeys(item for item in phones if item))[:100],
            "organization": (
                {
                    "name": organizations[0].get("name"),
                    "title": organizations[0].get("title"),
                }
                if organizations
                else None
            ),
        }


__all__ = [
    "DEFAULT_GOOGLE_FEATURES",
    "GOOGLE_SCOPE_FEATURES",
    "GOOGLE_MODEL_TOOL",
    "GoogleConnector",
    "GoogleOAuthConfig",
    "GoogleOAuthService",
    "GoogleProviderError",
    "google_model_tool",
]
