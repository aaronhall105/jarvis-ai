from __future__ import annotations

import base64
import json
import secrets
from collections import Counter
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.connectors import (
    ActionReceiptStore,
    CapabilityRequest,
    ConnectorRegistry,
    ExecutionStatus,
)
from app.google_integration import (
    SCOPE_CALENDAR_READ,
    SCOPE_CALENDAR_WRITE,
    SCOPE_CONTACTS_READ,
    SCOPE_EMAIL,
    SCOPE_GMAIL_COMPOSE,
    SCOPE_GMAIL_MODIFY,
    SCOPE_GMAIL_READ,
    SCOPE_OPENID,
    GoogleConnector,
    GoogleOAuthConfig,
    GoogleOAuthService,
)
from app.integration_accounts import CredentialCipher, IntegrationAccountStore, OAuthSessionError


ALL_SCOPES = (
    SCOPE_OPENID,
    SCOPE_EMAIL,
    SCOPE_GMAIL_READ,
    SCOPE_GMAIL_MODIFY,
    SCOPE_GMAIL_COMPOSE,
    SCOPE_CALENDAR_READ,
    SCOPE_CALENDAR_WRITE,
    SCOPE_CONTACTS_READ,
)


def _key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


class GoogleFixture:
    def __init__(self, scopes: tuple[str, ...] = ALL_SCOPES) -> None:
        self.scopes = scopes
        self.calls: Counter[str] = Counter()
        self.refresh_revoked = False
        self.sent_verified = True
        self.calendar_verified = True
        self.gmail_probe_status = 200
        self.gmail_probe_timeout = False
        self.gmail_search_status = 200
        self.gmail_search_malformed = False
        self.gmail_send_timeout = False
        self.calendar_probe_status = 200
        self.contacts_probe_status = 200
        self.archived = False
        self.event_deleted = False
        self.calendar_cancel_tombstone = False
        self.calendar_normalize_utc = False
        self.calendar_events_override: list[dict[str, object]] | None = None
        self.sent_message: dict[str, object] = {}
        self.forward_message: dict[str, object] = {}
        self.draft_message: dict[str, object] = {
            "id": "draft-message",
            "threadId": "thread-1",
            "payload": {
                "headers": [
                    {"name": "To", "value": "john@example.test"},
                    {"name": "Subject", "value": "Hello"},
                ],
                "mimeType": "text/plain",
                "body": {"data": "RHJhZnQgb25seQ"},
            },
        }
        self.event: dict[str, object] = {
            "id": "event-1",
            "status": "confirmed",
            "summary": "Garage",
            "start": {"dateTime": "2026-09-04T15:00:00+01:00"},
            "end": {"dateTime": "2026-09-04T16:00:00+01:00"},
        }

    def response(self, request: httpx.Request) -> httpx.Response:
        key = f"{request.method} {request.url.path}"
        self.calls[key] += 1
        path = request.url.path
        if path == "/token":
            if self.refresh_revoked and b"grant_type=refresh_token" in request.content:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(
                200,
                json={
                    "access_token": "access-super-secret",
                    "refresh_token": "refresh-super-secret",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": " ".join(self.scopes),
                },
            )
        if path == "/v1/userinfo":
            return httpx.Response(
                200,
                json={
                    "sub": "google-subject-1",
                    "email": "aaron@example.test",
                    "email_verified": True,
                    "name": "Aaron",
                },
            )
        if path == "/gmail/v1/users/me/drafts" and request.method == "POST":
            sent = json.loads(request.content)
            raw_message = sent.get("message") if isinstance(sent, dict) else None
            if isinstance(raw_message, dict) and raw_message.get("raw"):
                self.draft_message = self._message_from_raw(raw_message)
            return httpx.Response(200, json={"id": "draft-1", "message": {"id": "draft-message"}})
        if path == "/gmail/v1/users/me/drafts/draft-1" and request.method == "PUT":
            sent = json.loads(request.content)
            raw_message = sent.get("message") if isinstance(sent, dict) else None
            if isinstance(raw_message, dict) and raw_message.get("raw"):
                self.draft_message = self._message_from_raw(raw_message)
            return httpx.Response(200, json={"id": "draft-1", "message": {"id": "draft-message"}})
        if path == "/gmail/v1/users/me/drafts/draft-1":
            return httpx.Response(200, json={"id": "draft-1", "message": self.draft_message})
        if path == "/gmail/v1/users/me/drafts/send":
            if self.gmail_send_timeout:
                raise httpx.ReadTimeout("provider outcome is unknown", request=request)
            self.sent_message = dict(self.draft_message)
            self.sent_message["id"] = "sent-1"
            return httpx.Response(200, json={"id": "sent-1", "threadId": "thread-1"})
        if path == "/gmail/v1/users/me/messages/sent-1":
            labels = ["SENT"] if self.sent_verified else []
            return httpx.Response(200, json={**self.sent_message, "labelIds": labels})
        if path == "/gmail/v1/users/me/messages/forward-1":
            return httpx.Response(
                200,
                json={**self.forward_message, "id": "forward-1", "labelIds": ["SENT"]},
            )
        if path == "/gmail/v1/users/me/messages/send":
            sent = json.loads(request.content)
            if isinstance(sent, dict) and sent.get("raw"):
                self.forward_message = self._message_from_raw(sent)
            return httpx.Response(200, json={"id": "forward-1", "threadId": "thread-2"})
        if path == "/gmail/v1/users/me/messages" and request.method == "GET":
            if self.gmail_probe_timeout and request.url.params.get("maxResults") == "1":
                raise httpx.ConnectTimeout("gmail probe timed out", request=request)
            if self.gmail_probe_status != 200 and request.url.params.get("maxResults") == "1":
                return httpx.Response(self.gmail_probe_status, json={"error": "gmail unavailable"})
            if request.url.params.get("maxResults") != "1" and self.gmail_search_status != 200:
                return httpx.Response(
                    self.gmail_search_status,
                    json={"error": "gmail search unavailable"},
                )
            if request.url.params.get("maxResults") != "1" and self.gmail_search_malformed:
                return httpx.Response(200, text="not-json")
            return httpx.Response(
                200,
                json={"messages": [{"id": "message-2"}], "resultSizeEstimate": 1},
            )
        if path == "/gmail/v1/users/me/messages/message-2/modify":
            self.archived = True
            return httpx.Response(200, json={"id": "message-2", "labelIds": []})
        if path == "/gmail/v1/users/me/messages/message-2":
            return httpx.Response(
                200,
                json={
                    "id": "message-2",
                    "threadId": "thread-2",
                    "labelIds": [] if self.archived else ["INBOX"],
                    "snippet": "The garage can see you Friday.",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Garage <garage@example.test>"},
                            {"name": "Reply-To", "value": "replies@example.test"},
                            {"name": "Subject", "value": "Appointment"},
                            {"name": "Message-ID", "value": "<message-2@example.test>"},
                        ],
                        "mimeType": "text/plain",
                        "body": {"data": "VGhlIGdhcmFnZSBjYW4gc2VlIHlvdSBGcmlkYXku"},
                        "parts": [
                            {
                                "filename": "appointment.pdf",
                                "mimeType": "application/pdf",
                                "body": {"attachmentId": "attachment-1", "size": 321},
                            }
                        ],
                    },
                },
            )
        if path == "/gmail/v1/users/me/threads" and request.method == "GET":
            return httpx.Response(200, json={"threads": [{"id": "thread-2"}]})
        if path == "/gmail/v1/users/me/threads/thread-2":
            message = self.response(
                httpx.Request(
                    "GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages/message-2"
                )
            ).json()
            return httpx.Response(200, json={"id": "thread-2", "messages": [message]})
        if path == "/calendar/v3/users/me/calendarList":
            if self.calendar_probe_status != 200 and request.url.params.get("maxResults") == "1":
                return httpx.Response(
                    self.calendar_probe_status, json={"error": "calendar unavailable"}
                )
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "primary",
                            "summary": "Aaron",
                            "primary": True,
                            "accessRole": "owner",
                            "timeZone": "Europe/London",
                        }
                    ]
                },
            )
        if path == "/calendar/v3/calendars/primary/events" and request.method == "GET":
            return httpx.Response(
                200,
                json={"items": self.calendar_events_override or [self.event]},
            )
        if path == "/calendar/v3/calendars/primary/events" and request.method == "POST":
            requested_event = json.loads(request.content)
            requested_id = str(requested_event.get("id") or "")
            if requested_id and requested_id == self.event.get("id"):
                return httpx.Response(409, json={"error": "already exists"})
            self.event = {"id": "event-1", "status": "confirmed", **requested_event}
            if self.calendar_normalize_utc:
                for boundary in ("start", "end"):
                    value = self.event.get(boundary)
                    if isinstance(value, dict) and str(value.get("dateTime") or "").endswith(
                        "+00:00"
                    ):
                        value["dateTime"] = str(value["dateTime"])[:-6] + "Z"
            self.event_deleted = False
            return httpx.Response(200, json=self.event)
        if path == f"/calendar/v3/calendars/primary/events/{self.event.get('id')}":
            if request.method == "DELETE":
                self.event_deleted = True
                if self.calendar_cancel_tombstone:
                    self.event["status"] = "cancelled"
                return httpx.Response(204)
            if request.method == "PATCH":
                self.event.update(json.loads(request.content))
                if self.calendar_normalize_utc:
                    for boundary in ("start", "end"):
                        value = self.event.get(boundary)
                        if isinstance(value, dict) and str(value.get("dateTime") or "").endswith(
                            "+00:00"
                        ):
                            value["dateTime"] = str(value["dateTime"])[:-6] + "Z"
                return httpx.Response(200, json=self.event)
            if self.event_deleted:
                if self.calendar_cancel_tombstone:
                    return httpx.Response(200, json=self.event)
                return httpx.Response(404, json={"error": "not found"})
            observed = dict(self.event)
            if not self.calendar_verified:
                observed["summary"] = "Provider mismatch"
            return httpx.Response(200, json=observed)
        if path == "/calendar/v3/freeBusy":
            return httpx.Response(200, json={"calendars": {"primary": {"busy": []}}})
        if path == "/calendar/v3/users/me/settings/timezone":
            return httpx.Response(200, json={"value": "Europe/London"})
        if path == "/v1/people:searchContacts":
            first = {
                "person": {
                    "resourceName": "people/1",
                    "names": [{"displayName": "John Smith"}],
                    "emailAddresses": [{"value": "john.one@example.test"}],
                    "phoneNumbers": [{"value": "+441111111111"}],
                    "organizations": [{"name": "Example Ltd", "title": "Manager"}],
                }
            }
            second = {
                "person": {
                    "resourceName": "people/2",
                    "names": [{"displayName": "John Smith"}],
                    "emailAddresses": [{"value": "john.two@example.test"}],
                }
            }
            query = str(request.url.params.get("query") or "")
            results = [first] if query == "Unique John" else [first, second]
            if query == "Duplicate John":
                results = [first, first]
            return httpx.Response(
                200,
                json={"results": results},
            )
        if path == "/v1/people/me/connections":
            return httpx.Response(
                self.contacts_probe_status,
                json=(
                    {"connections": []}
                    if self.contacts_probe_status == 200
                    else {"error": "contacts unavailable"}
                ),
            )
        if path == "/revoke":
            return httpx.Response(200)
        raise AssertionError(f"Unexpected Google request: {request.method} {request.url}")

    @staticmethod
    def _message_from_raw(raw_message: dict[str, object]) -> dict[str, object]:
        encoded = str(raw_message["raw"])
        decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        parsed = BytesParser(policy=policy.default).parsebytes(decoded)
        content = parsed.get_content()
        body = content if isinstance(content, str) else content.decode(errors="replace")
        body_encoded = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
        return {
            "id": "draft-message",
            "threadId": raw_message.get("threadId"),
            "payload": {
                "headers": [{"name": key, "value": str(value)} for key, value in parsed.items()],
                "mimeType": "text/plain",
                "body": {"data": body_encoded},
            },
        }


async def connected_google(
    tmp_path: Path,
    fixture: GoogleFixture,
) -> tuple[IntegrationAccountStore, GoogleOAuthService, GoogleConnector, httpx.AsyncClient]:
    cipher = CredentialCipher(_key())
    store = IntegrationAccountStore(tmp_path / "accounts.db", cipher)
    await store.initialize()
    client = httpx.AsyncClient(transport=httpx.MockTransport(fixture.response))
    oauth = GoogleOAuthService(
        config=GoogleOAuthConfig(
            client_id="client-id.apps.googleusercontent.com",
            client_secret="client-super-secret",
            redirect_uri="https://core.example/api/integrations/google/callback",
        ),
        accounts=store,
        cipher=cipher,
        client=client,
    )
    started = await oauth.start(principal_id="aaron")
    query = parse_qs(urlsplit(started["authorization_url"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["access_type"] == ["offline"]
    assert query["redirect_uri"] == ["https://core.example/api/integrations/google/callback"]
    completed = await oauth.callback(state=query["state"][0], code="one-time-code")
    assert completed.status == "connected"
    connector = GoogleConnector(oauth=oauth, accounts=store, client=client)
    return store, oauth, connector, client


def test_google_oauth_config_rejects_redirect_and_android_return_attacks() -> None:
    assert GoogleOAuthConfig("id", "secret", "https://core.example/callback").configured
    assert not GoogleOAuthConfig("id", "secret", "https://user@evil.example/callback").configured
    assert not GoogleOAuthConfig(
        "id", "secret", "https://core.example/callback?next=https://evil.example"
    ).configured
    assert not GoogleOAuthConfig(
        "id",
        "secret",
        "https://core.example/callback",
        "jarvis://integrations/google.evil",
    ).configured


@pytest.mark.asyncio
async def test_oauth_callback_is_one_time_and_never_exposes_tokens(tmp_path: Path) -> None:
    fixture = GoogleFixture()
    store, oauth, connector, client = await connected_google(tmp_path, fixture)
    row = await store.account(principal_id="aaron", provider="google")
    assert row is not None
    credentials = await store.account_credentials(row["account_id"])
    assert credentials["refresh_token"] == "refresh-super-secret"
    account_status = await connector.account_status("aaron")
    assert account_status is not None
    status_json = json.dumps(account_status.as_dict())
    assert "access-super-secret" not in status_json
    assert "refresh-super-secret" not in status_json
    assert "client-super-secret" not in status_json

    started = await oauth.start(principal_id="aaron", features=("gmail_read",))
    state = parse_qs(urlsplit(started["authorization_url"]).query)["state"][0]
    await oauth.callback(state=state, code="another-code")
    with pytest.raises(OAuthSessionError, match="already been used"):
        await oauth.callback(state=state, code="replay-code")
    await client.aclose()


@pytest.mark.asyncio
async def test_partial_scopes_are_principal_isolated_and_capability_grounded(
    tmp_path: Path,
) -> None:
    fixture = GoogleFixture((SCOPE_OPENID, SCOPE_EMAIL, SCOPE_GMAIL_READ))
    _, _, connector, client = await connected_google(tmp_path, fixture)

    own = await connector.status_for_principal("aaron")
    other = await connector.status_for_principal("amber")

    assert own.available is True
    assert set(own.executable_capabilities or ()) == {
        "gmail.search",
        "gmail.read",
        "gmail.thread",
    }
    assert other.available is False
    assert other.executable_capabilities == ()
    await client.aclose()


@pytest.mark.asyncio
async def test_calendar_write_only_grant_uses_event_probe_not_calendar_list(
    tmp_path: Path,
) -> None:
    fixture = GoogleFixture((SCOPE_OPENID, SCOPE_EMAIL, SCOPE_CALENDAR_WRITE))
    _, _, connector, client = await connected_google(tmp_path, fixture)

    status = await connector.status_for_principal("aaron")

    assert set(status.executable_capabilities or ()) == {
        "calendar.create",
        "calendar.update",
        "calendar.cancel",
    }
    assert fixture.calls["GET /calendar/v3/calendars/primary/events"] >= 1
    assert fixture.calls["GET /calendar/v3/users/me/calendarList"] == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_gmail_and_calendar_writes_require_verified_receipts_and_are_idempotent(
    tmp_path: Path,
) -> None:
    fixture = GoogleFixture()
    _, _, connector, client = await connected_google(tmp_path, fixture)
    receipts = ActionReceiptStore(tmp_path / "receipts.db")
    await receipts.initialize()
    registry = ConnectorRegistry(receipt_store=receipts, health_ttl_seconds=60)
    registry.register(connector)

    draft = await registry.execute(
        CapabilityRequest(
            capability_id="gmail.draft",
            payload={"to": "john@example.test", "subject": "Hello", "body": "Draft only"},
            principal_id="aaron",
            conversation_id="usr:aaron:conversation-1",
            idempotency_key="draft-key",
        ),
        refresh_health=True,
    )
    assert draft.status is ExecutionStatus.VERIFIED
    assert draft.data["status"] == "drafted"
    assert draft.data.get("status") != "sent"

    send_request = CapabilityRequest(
        capability_id="gmail.send",
        payload={"draft_id": "draft-1"},
        principal_id="aaron",
        conversation_id="usr:aaron:conversation-1",
        confirmed=True,
        idempotency_key="send-key",
    )
    sent = await registry.execute(send_request)
    repeated = await registry.execute(send_request)
    assert sent.status is ExecutionStatus.VERIFIED
    assert sent.data["status"] == "sent"
    assert repeated.status is ExecutionStatus.VERIFIED
    assert fixture.calls["POST /gmail/v1/users/me/drafts/send"] == 1

    event_request = CapabilityRequest(
        capability_id="calendar.create",
        payload={
            "summary": "Garage",
            "start": {"dateTime": "2026-09-04T15:00:00+01:00"},
            "end": {"dateTime": "2026-09-04T16:00:00+01:00"},
        },
        principal_id="aaron",
        conversation_id="usr:aaron:conversation-1",
        confirmed=True,
        idempotency_key="calendar-key",
    )
    created = await registry.execute(event_request)
    duplicate = await registry.execute(event_request)
    assert created.status is ExecutionStatus.VERIFIED
    assert duplicate.status is ExecutionStatus.VERIFIED
    assert fixture.calls["POST /calendar/v3/calendars/primary/events"] == 1
    assert "access-super-secret" not in json.dumps(created.as_dict())
    await client.aclose()


@pytest.mark.asyncio
async def test_calendar_provider_idempotency_recovers_existing_matching_event(
    tmp_path: Path,
) -> None:
    fixture = GoogleFixture()
    _, _, connector, client = await connected_google(tmp_path, fixture)
    request = CapabilityRequest(
        capability_id="calendar.create",
        payload={
            "summary": "Jarvis idempotency test",
            "start": {"dateTime": "2026-09-04T15:00:00+01:00"},
            "end": {"dateTime": "2026-09-04T16:00:00+01:00"},
        },
        principal_id="aaron",
        conversation_id="usr:aaron:conversation-1",
        confirmed=True,
        idempotency_key="stable-calendar-event",
    )

    first_receipts = ActionReceiptStore(tmp_path / "receipts-first.db")
    await first_receipts.initialize()
    first_registry = ConnectorRegistry(receipt_store=first_receipts)
    first_registry.register(connector)
    first = await first_registry.execute(request, refresh_health=True)

    # Model a restart after provider success but without access to the first
    # local receipt. The deterministic provider ID turns the retry into a
    # readback of the already-created matching event.
    recovery_receipts = ActionReceiptStore(tmp_path / "receipts-recovery.db")
    await recovery_receipts.initialize()
    recovery_registry = ConnectorRegistry(receipt_store=recovery_receipts)
    recovery_registry.register(connector)
    recovered = await recovery_registry.execute(request, refresh_health=True)

    assert first.status is ExecutionStatus.VERIFIED
    assert recovered.status is ExecutionStatus.VERIFIED
    assert first.provider_reference == recovered.provider_reference
    assert fixture.calls["POST /calendar/v3/calendars/primary/events"] == 2
    assert (
        fixture.calls[f"GET /calendar/v3/calendars/primary/events/{first.provider_reference}"] >= 2
    )
    await client.aclose()


@pytest.mark.asyncio
async def test_write_receipts_remain_unverified_without_provider_state_evidence(
    tmp_path: Path,
) -> None:
    fixture = GoogleFixture()
    _, _, connector, client = await connected_google(tmp_path, fixture)
    receipts = ActionReceiptStore(tmp_path / "receipts.db")
    await receipts.initialize()
    registry = ConnectorRegistry(receipt_store=receipts)
    registry.register(connector)

    fixture.sent_verified = False
    sent = await registry.execute(
        CapabilityRequest(
            capability_id="gmail.send",
            payload={"draft_id": "draft-1"},
            principal_id="aaron",
            confirmed=True,
            idempotency_key="unverified-send",
        ),
        refresh_health=True,
    )
    assert sent.status is ExecutionStatus.ACCEPTED_UNVERIFIED
    assert "Sent mail" in str(sent.error)

    fixture.sent_verified = True
    fixture.calendar_verified = False
    created = await registry.execute(
        CapabilityRequest(
            capability_id="calendar.create",
            payload={
                "summary": "Garage",
                "start": {"dateTime": "2026-09-04T15:00:00+01:00"},
                "end": {"dateTime": "2026-09-04T16:00:00+01:00"},
            },
            principal_id="aaron",
            confirmed=True,
            idempotency_key="unverified-calendar",
        )
    )
    assert created.status is ExecutionStatus.ACCEPTED_UNVERIFIED
    assert "fields did not match" in str(created.error)
    await client.aclose()


@pytest.mark.asyncio
async def test_gmail_search_returns_provider_message_summaries(tmp_path: Path) -> None:
    fixture = GoogleFixture()
    _, _, connector, client = await connected_google(tmp_path, fixture)
    receipts = ActionReceiptStore(tmp_path / "receipts.db")
    registry = ConnectorRegistry(receipt_store=receipts)
    registry.register(connector)

    result = await registry.execute(
        CapabilityRequest(
            capability_id="gmail.search",
            payload={"query": "from:garage newer_than:7d"},
            principal_id="aaron",
        ),
        refresh_health=True,
    )

    assert result.success is True
    assert result.data["messages"][0]["from"] == "Garage <garage@example.test>"
    assert result.data["messages"][0]["subject"] == "Appointment"
    assert "Friday" in result.data["messages"][0]["snippet"]
    assert result.data["messages"][0]["attachments"] == [
        {
            "filename": "appointment.pdf",
            "mime_type": "application/pdf",
            "size": 321,
            "attachment_id": "attachment-1",
        }
    ]

    inbox = await registry.execute(
        CapabilityRequest(
            capability_id="gmail.search",
            payload={},
            principal_id="aaron",
        )
    )
    assert inbox.data["query"] == "in:inbox"
    await client.aclose()


@pytest.mark.asyncio
async def test_gmail_read_thread_draft_edit_reply_forward_and_archive(
    tmp_path: Path,
) -> None:
    fixture = GoogleFixture()
    _, _, connector, client = await connected_google(tmp_path, fixture)
    receipts = ActionReceiptStore(tmp_path / "receipts.db")
    await receipts.initialize()
    registry = ConnectorRegistry(receipt_store=receipts)
    registry.register(connector)

    read = await registry.execute(
        CapabilityRequest(
            capability_id="gmail.read",
            payload={"message_id": "message-2"},
            principal_id="aaron",
        ),
        refresh_health=True,
    )
    thread_search = await registry.execute(
        CapabilityRequest(
            capability_id="gmail.thread",
            payload={"query": "from:garage", "limit": 10},
            principal_id="aaron",
        )
    )
    thread_read = await registry.execute(
        CapabilityRequest(
            capability_id="gmail.thread",
            payload={"thread_id": "thread-2"},
            principal_id="aaron",
        )
    )
    edited = await registry.execute(
        CapabilityRequest(
            capability_id="gmail.draft",
            payload={
                "draft_id": "draft-1",
                "to": "john@example.test",
                "subject": "Updated",
                "body": "Updated body",
            },
            principal_id="aaron",
            idempotency_key="edit-draft",
        )
    )
    reply = await registry.execute(
        CapabilityRequest(
            capability_id="gmail.reply",
            payload={"message_id": "message-2", "body": "Thanks"},
            principal_id="aaron",
            idempotency_key="reply-draft",
        )
    )
    forwarded = await registry.execute(
        CapabilityRequest(
            capability_id="gmail.forward",
            payload={
                "message_id": "message-2",
                "to": "john@example.test",
                "body": "For reference",
            },
            principal_id="aaron",
            confirmed=True,
            idempotency_key="forward-message",
        )
    )
    archived = await registry.execute(
        CapabilityRequest(
            capability_id="gmail.archive",
            payload={"message_id": "message-2"},
            principal_id="aaron",
            confirmed=True,
            idempotency_key="archive-message",
        )
    )

    assert "Friday" in read.data["body"]
    assert thread_search.data["thread_ids"] == ["thread-2"]
    assert thread_read.data["latest_message_id"] == "message-2"
    assert edited.status is ExecutionStatus.VERIFIED
    assert edited.data["status"] == "drafted"
    assert reply.status is ExecutionStatus.VERIFIED
    assert reply.data["status"] == "drafted"
    assert forwarded.status is ExecutionStatus.VERIFIED
    assert forwarded.data["status"] == "sent"
    assert archived.status is ExecutionStatus.VERIFIED
    assert fixture.calls["POST /gmail/v1/users/me/messages/send"] == 1
    assert fixture.calls["POST /gmail/v1/users/me/messages/message-2/modify"] == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_calendar_read_write_timezone_and_cancellation_surface_verified_evidence(
    tmp_path: Path,
) -> None:
    fixture = GoogleFixture()
    _, _, connector, client = await connected_google(tmp_path, fixture)
    receipts = ActionReceiptStore(tmp_path / "receipts.db")
    await receipts.initialize()
    registry = ConnectorRegistry(receipt_store=receipts)
    registry.register(connector)

    calendars = await registry.execute(
        CapabilityRequest(capability_id="calendar.list", payload={}, principal_id="aaron"),
        refresh_health=True,
    )
    events = await registry.execute(
        CapabilityRequest(
            capability_id="calendar.list",
            payload={"events": True, "calendar_id": "primary"},
            principal_id="aaron",
        )
    )
    event = await registry.execute(
        CapabilityRequest(
            capability_id="calendar.read",
            payload={"event_id": "event-1"},
            principal_id="aaron",
        )
    )
    search = await registry.execute(
        CapabilityRequest(
            capability_id="calendar.search",
            payload={"query": "Garage"},
            principal_id="aaron",
        )
    )
    availability = await registry.execute(
        CapabilityRequest(
            capability_id="calendar.availability",
            payload={
                "timeMin": "2026-09-04T00:00:00Z",
                "timeMax": "2026-09-05T00:00:00Z",
                "timeZone": "Europe/London",
            },
            principal_id="aaron",
        )
    )
    timezone_result = await registry.execute(
        CapabilityRequest(capability_id="calendar.timezone", payload={}, principal_id="aaron")
    )
    updated = await registry.execute(
        CapabilityRequest(
            capability_id="calendar.update",
            payload={"event_id": "event-1", "changes": {"summary": "Updated Garage"}},
            principal_id="aaron",
            confirmed=True,
            idempotency_key="update-event",
        )
    )
    cancelled = await registry.execute(
        CapabilityRequest(
            capability_id="calendar.cancel",
            payload={"event_id": "event-1"},
            principal_id="aaron",
            confirmed=True,
            idempotency_key="cancel-event",
        )
    )

    assert calendars.data["calendars"][0]["calendar_id"] == "primary"
    assert events.data["events"][0]["event_id"] == "event-1"
    assert event.data["event"]["event_id"] == "event-1"
    assert search.data["events"][0]["summary"] == "Garage"
    assert search.data["resolved"] is True
    assert search.data["ambiguous"] is False
    assert availability.data["calendars"]["primary"]["busy"] == []
    assert timezone_result.data["time_zone"] == "Europe/London"
    assert updated.status is ExecutionStatus.VERIFIED
    assert updated.data["event"]["summary"] == "Updated Garage"
    assert cancelled.status is ExecutionStatus.VERIFIED
    assert cancelled.data["status"] == "deleted"
    await client.aclose()


@pytest.mark.asyncio
async def test_calendar_verification_accepts_normalized_utc_and_cancelled_tombstone(
    tmp_path: Path,
) -> None:
    fixture = GoogleFixture()
    fixture.calendar_normalize_utc = True
    fixture.calendar_cancel_tombstone = True
    _, _, connector, client = await connected_google(tmp_path, fixture)
    receipts = ActionReceiptStore(tmp_path / "receipts.db")
    registry = ConnectorRegistry(receipt_store=receipts)
    registry.register(connector)
    request = CapabilityRequest(
        capability_id="calendar.create",
        payload={
            "summary": "Jarvis Integration Test",
            "start": {"dateTime": "2026-09-04T15:00:00+00:00", "timeZone": "UTC"},
            "end": {"dateTime": "2026-09-04T16:00:00+00:00", "timeZone": "UTC"},
        },
        principal_id="aaron",
        confirmed=True,
        idempotency_key="normalized-calendar-event",
    )

    created = await registry.execute(request, refresh_health=True)
    cancelled = await registry.execute(
        CapabilityRequest(
            capability_id="calendar.cancel",
            payload={"event_id": created.provider_reference},
            principal_id="aaron",
            confirmed=True,
            idempotency_key="cancel-normalized-calendar-event",
        )
    )

    assert created.status is ExecutionStatus.VERIFIED
    assert cancelled.status is ExecutionStatus.VERIFIED
    await client.aclose()


@pytest.mark.asyncio
async def test_calendar_search_marks_multiple_events_ambiguous(tmp_path: Path) -> None:
    fixture = GoogleFixture()
    _, _, connector, client = await connected_google(tmp_path, fixture)
    fixture.calendar_events_override = [
        {**fixture.event, "id": "event-1", "summary": "Dentist consultation"},
        {**fixture.event, "id": "event-2", "summary": "Dentist follow-up"},
    ]
    receipts = ActionReceiptStore(tmp_path / "receipts.db")
    registry = ConnectorRegistry(receipt_store=receipts)
    registry.register(connector)

    result = await registry.execute(
        CapabilityRequest(
            capability_id="calendar.search",
            payload={"query": "Dentist"},
            principal_id="aaron",
        ),
        refresh_health=True,
    )

    assert result.data["count"] == 2
    assert result.data["resolved"] is False
    assert result.data["ambiguous"] is True
    assert result.provider_reference is None
    await client.aclose()


@pytest.mark.asyncio
async def test_gmail_rejects_multiple_or_header_injection_recipients(tmp_path: Path) -> None:
    fixture = GoogleFixture()
    _, _, connector, client = await connected_google(tmp_path, fixture)
    capability = next(
        item for item in connector.capabilities if item.capability_id == "gmail.draft"
    )

    for recipient in (
        "one@example.test, two@example.test",
        "victim@example.test\nBcc: attacker@example.test",
    ):
        result = await connector.execute(
            capability,
            CapabilityRequest(
                capability_id="gmail.draft",
                payload={"to": recipient, "subject": "Hello", "body": "Body"},
                principal_id="aaron",
            ),
        )
        assert result.status.value == "failed"
    assert fixture.calls["POST /gmail/v1/users/me/drafts"] == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_provider_failures_are_truthful_and_writes_are_never_blindly_retried(
    tmp_path: Path,
) -> None:
    fixture = GoogleFixture()
    _, _, connector, client = await connected_google(tmp_path, fixture)
    search_capability = next(
        item for item in connector.capabilities if item.capability_id == "gmail.search"
    )
    send_capability = next(
        item for item in connector.capabilities if item.capability_id == "gmail.send"
    )

    fixture.gmail_search_status = 429
    rate_limited = await connector.execute(
        search_capability,
        CapabilityRequest(
            capability_id="gmail.search",
            payload={"query": "in:inbox"},
            principal_id="aaron",
        ),
    )
    assert rate_limited.status.value == "failed"
    assert rate_limited.retryable is True
    assert "HTTP 429" in str(rate_limited.error)

    fixture.gmail_search_status = 200
    fixture.gmail_search_malformed = True
    malformed = await connector.execute(
        search_capability,
        CapabilityRequest(
            capability_id="gmail.search",
            payload={"query": "in:inbox"},
            principal_id="aaron",
        ),
    )
    assert malformed.status.value == "failed"
    assert malformed.retryable is False
    assert malformed.error == "Google API returned malformed JSON"

    fixture.gmail_search_malformed = False
    fixture.gmail_send_timeout = True
    unknown = await connector.execute(
        send_capability,
        CapabilityRequest(
            capability_id="gmail.send",
            payload={"draft_id": "draft-1"},
            principal_id="aaron",
            confirmed=True,
        ),
    )
    assert unknown.status.value == "outcome_unknown"
    assert unknown.error == "Google provider transport failed"
    assert fixture.calls["POST /gmail/v1/users/me/drafts/send"] == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_contacts_are_provider_verified_and_ambiguity_never_invents_details(
    tmp_path: Path,
) -> None:
    fixture = GoogleFixture()
    _, _, connector, client = await connected_google(tmp_path, fixture)
    receipts = ActionReceiptStore(tmp_path / "receipts.db")
    registry = ConnectorRegistry(receipt_store=receipts)
    registry.register(connector)

    result = await registry.execute(
        CapabilityRequest(
            capability_id="contacts.resolve",
            payload={"query": "John Smith"},
            principal_id="aaron",
        ),
        refresh_health=True,
    )

    assert result.success is True
    assert result.data["resolved"] is False
    assert result.data["ambiguous"] is True
    assert len(result.data["matches"]) == 2
    assert "john.one@example.test" in json.dumps(result.data)
    assert "invented" not in json.dumps(result.data)
    await client.aclose()


@pytest.mark.asyncio
async def test_contacts_unique_and_duplicate_results_preserve_provider_details(
    tmp_path: Path,
) -> None:
    fixture = GoogleFixture()
    _, _, connector, client = await connected_google(tmp_path, fixture)
    registry = ConnectorRegistry(receipt_store=ActionReceiptStore(tmp_path / "receipts.db"))
    registry.register(connector)

    unique = await registry.execute(
        CapabilityRequest(
            capability_id="contacts.resolve",
            payload={"query": "Unique John"},
            principal_id="aaron",
        ),
        refresh_health=True,
    )
    duplicate = await registry.execute(
        CapabilityRequest(
            capability_id="contacts.search",
            payload={"query": "Duplicate John"},
            principal_id="aaron",
        )
    )

    assert unique.data["resolved"] is True
    assert unique.data["contact"]["email_addresses"] == ["john.one@example.test"]
    assert unique.data["contact"]["organization"] == {
        "name": "Example Ltd",
        "title": "Manager",
    }
    assert unique.data["contact"]["phone_numbers"] == ["+441111111111"]
    assert duplicate.data["count"] == 1
    assert duplicate.data["contacts"][0]["resource_name"] == "people/1"
    await client.aclose()


@pytest.mark.asyncio
async def test_tokens_are_not_healthy_until_each_granted_product_is_probed(
    tmp_path: Path,
) -> None:
    fixture = GoogleFixture()
    store, _, connector, client = await connected_google(tmp_path, fixture)
    row = await store.account(principal_id="aaron", provider="google")
    assert row is not None
    assert bool(row["authenticated"]) is True
    assert bool(row["healthy"]) is False
    assert row["last_health_check"] is None

    fixture.calendar_probe_status = 503
    status = await connector.status_for_principal("aaron")
    services = connector.service_health("aaron")
    credential_status = await connector.credential_status("aaron")

    assert status.available is True
    assert "gmail.search" in (status.executable_capabilities or ())
    assert "contacts.resolve" in (status.executable_capabilities or ())
    assert "calendar.list" not in (status.executable_capabilities or ())
    assert services["gmail"]["healthy"] is True
    assert services["calendar"]["healthy"] is False
    assert services["contacts"]["healthy"] is True
    assert credential_status is not None
    assert credential_status["access_token_present"] is True
    assert credential_status["refresh_token_present"] is True
    assert "access-super-secret" not in json.dumps(credential_status)
    assert "refresh-super-secret" not in json.dumps(credential_status)
    await client.aclose()


@pytest.mark.asyncio
async def test_product_api_403_degrades_only_that_service_without_revoking_account(
    tmp_path: Path,
) -> None:
    fixture = GoogleFixture()
    store, _, connector, client = await connected_google(tmp_path, fixture)
    fixture.gmail_probe_status = 403

    status = await connector.status_for_principal("aaron")
    account = await connector.account_status("aaron")

    assert status.authenticated is True
    assert status.available is True
    assert not any(
        capability.startswith("gmail.") for capability in status.executable_capabilities or ()
    )
    assert account is not None and account.reauthorization_required is False
    assert connector.service_health("aaron")["gmail"]["healthy"] is False
    stored = await store.account(principal_id="aaron", provider="google")
    assert stored is not None and bool(stored["authenticated"]) is True
    await client.aclose()


@pytest.mark.asyncio
async def test_product_probe_transport_failure_is_safe_degraded_state(tmp_path: Path) -> None:
    fixture = GoogleFixture()
    _, _, connector, client = await connected_google(tmp_path, fixture)
    fixture.gmail_probe_timeout = True

    status = await connector.status_for_principal("aaron")
    gmail = connector.service_health("aaron")["gmail"]

    assert status.available is True
    assert not any(
        capability.startswith("gmail.") for capability in status.executable_capabilities or ()
    )
    assert gmail["healthy"] is False
    assert gmail["last_error_category"] == "transport_unavailable"
    assert gmail["last_probe_at"] is not None
    assert gmail["last_successful_probe_at"] is None
    assert "timed out" not in json.dumps(gmail)
    await client.aclose()


@pytest.mark.asyncio
async def test_credential_diagnostic_fails_closed_when_envelope_cannot_decrypt(
    tmp_path: Path,
) -> None:
    fixture = GoogleFixture()
    store, _, connector, client = await connected_google(tmp_path, fixture)
    row = await store.account(principal_id="aaron", provider="google")
    assert row is not None
    connection = store._db()
    try:
        connection.execute(
            "UPDATE integration_accounts SET encrypted_credentials=? WHERE account_id=?",
            ("v1.invalid-envelope", row["account_id"]),
        )
        connection.commit()
    finally:
        connection.close()

    status = await connector.credential_status("aaron")

    assert status == {
        "access_token_present": False,
        "refresh_token_present": False,
        "expires_at": None,
        "expired": False,
        "expires_soon": False,
        "error_category": "credential_unavailable",
    }
    assert "invalid-envelope" not in json.dumps(status)
    await client.aclose()


@pytest.mark.asyncio
async def test_revoked_refresh_token_requires_reconnect(tmp_path: Path) -> None:
    fixture = GoogleFixture()
    store, _, connector, client = await connected_google(tmp_path, fixture)
    row = await store.account(principal_id="aaron", provider="google")
    assert row is not None
    credentials = await store.account_credentials(row["account_id"])
    credentials["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    await store.update_credentials(row["account_id"], credentials)
    fixture.refresh_revoked = True

    status = await connector.status_for_principal("aaron")
    account = await connector.account_status("aaron")

    assert status.available is False
    assert status.authenticated is False
    assert account is not None and account.reauthorization_required is True
    assert "refresh-super-secret" not in json.dumps(status.as_dict())
    await client.aclose()


@pytest.mark.asyncio
async def test_disconnect_revokes_provider_token_and_removes_only_owned_account(
    tmp_path: Path,
) -> None:
    fixture = GoogleFixture()
    store, _, connector, client = await connected_google(tmp_path, fixture)
    row = await store.account(principal_id="aaron", provider="google")
    assert row is not None

    other = await connector.disconnect(principal_id="amber", account_id=row["account_id"])
    result = await connector.disconnect(principal_id="aaron", account_id=row["account_id"])

    assert other["disconnected"] is False
    assert result == {
        "disconnected": True,
        "revocation_confirmed": True,
        "revocation_status": "confirmed",
    }
    assert fixture.calls["POST /revoke"] == 1
    assert await store.account(principal_id="aaron", provider="google") is None
    assert (await connector.status_for_principal("aaron")).available is False
    await client.aclose()
