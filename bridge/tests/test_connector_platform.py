from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from app.connectors import (
    ActionReceiptStore,
    CapabilityAccess,
    CapabilityMetadata,
    CapabilityRequest,
    ConfirmationMode,
    Connector,
    ConnectorRegistry,
    ConnectorResult,
    CredentialResolver,
    ExecutionStatus,
    IdempotencyConflict,
    ProviderStatus,
    ReceiptStatus,
    RiskLevel,
    VerificationMode,
    VerificationResult,
    redact_secrets,
    redact_text,
)

READ_CAPABILITY = CapabilityMetadata(
    capability_id="fixture.search",
    provider_id="fixture",
    name="Search fixture",
    description="Read deterministic records",
    access=CapabilityAccess.READ,
    required_scopes=frozenset({"fixture.read"}),
    risk=RiskLevel.LOW,
    confirmation=ConfirmationMode.NONE,
    supports_async=True,
    verification=VerificationMode.NONE,
    timeout_seconds=0.1,
)
WRITE_CAPABILITY = CapabilityMetadata(
    capability_id="fixture.publish",
    provider_id="fixture",
    name="Publish fixture",
    description="Write a deterministic record",
    access=CapabilityAccess.WRITE,
    required_scopes=frozenset({"fixture.write"}),
    risk=RiskLevel.HIGH,
    confirmation=ConfirmationMode.REQUIRED,
    supports_async=False,
    verification=VerificationMode.REQUIRED,
    timeout_seconds=0.1,
)
UNVERIFIED_WRITE_CAPABILITY = CapabilityMetadata(
    capability_id="fixture.draft",
    provider_id="fixture",
    name="Create draft",
    access=CapabilityAccess.WRITE,
    required_scopes=frozenset({"fixture.write"}),
    risk=RiskLevel.MEDIUM,
    confirmation=ConfirmationMode.CONTEXTUAL,
    verification=VerificationMode.NONE,
    timeout_seconds=0.1,
)


class FakeConnector(Connector):
    def __init__(
        self,
        *,
        configured: bool = True,
        authenticated: bool = True,
        healthy: bool = True,
        scopes: frozenset[str] = frozenset({"fixture.read", "fixture.write"}),
        executable: tuple[str, ...] | None = None,
        health_reason: str | None = None,
        setup_requirements: tuple[str, ...] = (),
        capabilities: tuple[CapabilityMetadata, ...] = (
            READ_CAPABILITY,
            WRITE_CAPABILITY,
            UNVERIFIED_WRITE_CAPABILITY,
        ),
    ) -> None:
        super().__init__(
            provider_id="fixture",
            name="Fixture Provider",
            capabilities=capabilities,
        )
        self.configured = configured
        self.authenticated = authenticated
        self.healthy = healthy
        self.scopes = scopes
        self.executable = executable
        self.health_reason = health_reason
        self.setup_requirements = setup_requirements
        self.status_calls = 0
        self.execute_calls: list[str] = []
        self.execute_results: list[ConnectorResult | BaseException] = []
        self.verify_result = VerificationResult.verified({"state": "visible"})
        self.status_error: BaseException | None = None
        self.status_delay = 0.0
        self.execute_delay = 0.0
        self.before_execute: Callable[[], Awaitable[None]] | None = None
        self.health_cache_invalidations = 0

    def invalidate_health_cache(self) -> None:
        self.health_cache_invalidations += 1

    async def status(self) -> ProviderStatus:
        self.status_calls += 1
        if self.status_delay:
            await asyncio.sleep(self.status_delay)
        if self.status_error is not None:
            raise self.status_error
        return ProviderStatus(
            provider_id=self.provider_id,
            name=self.name,
            configured=self.configured,
            authenticated=self.authenticated,
            healthy=self.healthy,
            health_reason=self.health_reason,
            setup_requirements=self.setup_requirements,
            scopes=self.scopes,
            executable_capabilities=self.executable,
        )

    async def execute(
        self,
        capability: CapabilityMetadata,
        request: CapabilityRequest,
    ) -> ConnectorResult:
        self.execute_calls.append(capability.capability_id)
        if self.before_execute is not None:
            await self.before_execute()
        if self.execute_delay:
            await asyncio.sleep(self.execute_delay)
        outcome: ConnectorResult | BaseException
        if self.execute_results:
            outcome = self.execute_results.pop(0)
        else:
            outcome = ConnectorResult.succeeded(
                {"echo": dict(request.payload)},
                provider_reference="provider-123",
            )
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def verify(
        self,
        capability: CapabilityMetadata,
        request: CapabilityRequest,
        result: ConnectorResult,
    ) -> VerificationResult:
        return self.verify_result


def make_registry(
    tmp_path: Path,
    connector: FakeConnector,
    **kwargs: Any,
) -> tuple[ConnectorRegistry, ActionReceiptStore]:
    store = ActionReceiptStore(tmp_path / "receipts.db")
    registry = ConnectorRegistry(
        receipt_store=store,
        health_ttl_seconds=60,
        read_retry_delay_seconds=0,
        **kwargs,
    )
    registry.register(connector)
    return registry, store


def test_capability_metadata_is_immutable_and_carries_permission_contract() -> None:
    assert WRITE_CAPABILITY.access is CapabilityAccess.WRITE
    assert WRITE_CAPABILITY.required_scopes == frozenset({"fixture.write"})
    assert WRITE_CAPABILITY.risk is RiskLevel.HIGH
    assert WRITE_CAPABILITY.confirmation is ConfirmationMode.REQUIRED
    assert WRITE_CAPABILITY.supports_async is False
    assert WRITE_CAPABILITY.verification is VerificationMode.REQUIRED
    with pytest.raises(FrozenInstanceError):
        WRITE_CAPABILITY.name = "changed"  # type: ignore[misc]


def test_repeatable_capability_requires_explicit_safe_monitor_policy() -> None:
    repeatable = CapabilityMetadata(
        capability_id="fixture.poll",
        provider_id="fixture",
        name="Poll fixture",
        repeatable=True,
        minimum_poll_interval_seconds=60,
        maximum_monitor_polls=10,
        monitor_ttl_seconds=3600,
        monitor_value_paths=("state.value",),
    )

    assert repeatable.as_dict()["repeatable"] is True
    assert repeatable.as_dict()["minimum_poll_interval_seconds"] == 60
    assert repeatable.as_dict()["monitor_value_paths"] == ["state.value"]
    with pytest.raises(ValueError, match="polling interval, poll limit"):
        CapabilityMetadata(
            capability_id="fixture.unsafe_poll",
            provider_id="fixture",
            name="Unsafe poll",
            repeatable=True,
        )
    with pytest.raises(ValueError, match="Only read capabilities"):
        CapabilityMetadata(
            capability_id="fixture.write_poll",
            provider_id="fixture",
            name="Unsafe write poll",
            access=CapabilityAccess.WRITE,
            repeatable=True,
            minimum_poll_interval_seconds=60,
            maximum_monitor_polls=10,
            monitor_ttl_seconds=3600,
            monitor_value_paths=("state",),
        )


@pytest.mark.asyncio
async def test_configured_provider_exposes_capabilities_and_caches_live_health(
    tmp_path: Path,
) -> None:
    connector = FakeConnector()
    registry, _ = make_registry(tmp_path, connector)

    status = await registry.provider_status("fixture")
    lookup = await registry.get_capability("fixture.search")
    capabilities = await registry.executable_capabilities()
    cached = await registry.provider_status("fixture")

    assert status.available is True
    assert set(status.executable_capabilities or ()) == {
        "fixture.search",
        "fixture.publish",
        "fixture.draft",
    }
    assert lookup is READ_CAPABILITY
    assert {item.capability_id for item in capabilities} == {
        "fixture.search",
        "fixture.publish",
        "fixture.draft",
    }
    assert cached is status
    assert connector.status_calls == 1

    refreshed = await registry.provider_status("fixture", refresh=True)
    assert refreshed.available is True
    assert connector.status_calls == 2
    assert connector.health_cache_invalidations == 1


@pytest.mark.asyncio
async def test_unconfigured_provider_exposes_no_executable_capability_but_setup_is_discoverable(
    tmp_path: Path,
) -> None:
    connector = FakeConnector(
        configured=False,
        authenticated=False,
        healthy=False,
        setup_requirements=("Configure FIXTURE_TOKEN",),
    )
    registry, _ = make_registry(tmp_path, connector)

    status = await registry.provider_status("fixture")
    snapshot = await registry.capability_snapshot()

    assert status.configured is False
    assert status.authenticated is False
    assert status.healthy is False
    assert status.executable_capabilities == ()
    assert set(status.potential_capabilities) == {
        "fixture.search",
        "fixture.publish",
        "fixture.draft",
    }
    assert await registry.get_capability("fixture.search") is None
    assert await registry.executable_capabilities() == ()
    assert all(item["available"] is False for item in snapshot)


@pytest.mark.asyncio
async def test_unhealthy_provider_blocks_execution_and_health_snapshot_is_truthful(
    tmp_path: Path,
) -> None:
    connector = FakeConnector(healthy=False, health_reason="upstream offline")
    registry, _ = make_registry(tmp_path, connector)

    result = await registry.execute("fixture.search", {"query": "anything"})
    health = await registry.health_snapshot()

    assert result.status is ExecutionStatus.UNAVAILABLE
    assert result.success is False
    assert connector.execute_calls == []
    assert health["healthy"] is False
    assert health["available_provider_count"] == 0
    assert health["providers"][0]["health_reason"] == "upstream offline"


@pytest.mark.asyncio
async def test_missing_provider_or_caller_scope_blocks_capability(
    tmp_path: Path,
) -> None:
    connector = FakeConnector(scopes=frozenset({"fixture.read"}))
    registry, _ = make_registry(tmp_path, connector)

    status = await registry.provider_status("fixture")
    provider_blocked = await registry.execute(
        "fixture.publish",
        {"body": "hello"},
        confirmed=True,
        idempotency_key="scope-provider",
    )
    caller_blocked = await registry.execute(
        "fixture.search",
        {"query": "hello"},
        allowed_scopes=frozenset(),
    )

    assert "fixture.publish" not in (status.executable_capabilities or ())
    assert provider_blocked.status is ExecutionStatus.UNAVAILABLE
    assert provider_blocked.receipt.status is ReceiptStatus.REJECTED
    assert caller_blocked.status is ExecutionStatus.REJECTED
    assert connector.execute_calls == []


@pytest.mark.asyncio
async def test_health_failure_is_cached_unhealthy_and_secret_safe(
    tmp_path: Path,
) -> None:
    connector = FakeConnector()
    connector.status_error = RuntimeError("authorization: Bearer live-health-token")
    registry, _ = make_registry(tmp_path, connector)

    status = await registry.provider_status("fixture", refresh=True)
    encoded = json.dumps(status.as_dict())

    assert status.healthy is False
    assert status.executable_capabilities == ()
    assert "live-health-token" not in encoded
    assert "[REDACTED]" in encoded


def test_recursive_and_free_text_redaction_covers_common_secret_shapes() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signaturevalue"
    source = {
        "outer": {
            "api_key": "plain-secret",
            "message": (
                "Authorization: Bearer bearer-value "
                "url=https://example.test/?access_token=query-value "
                f"jwt={jwt}"
            ),
        },
        "items": [{"password": "password-value"}],
    }

    redacted = redact_secrets(source)
    encoded = json.dumps(redacted)

    for secret in (
        "plain-secret",
        "bearer-value",
        "query-value",
        "password-value",
        jwt,
    ):
        assert secret not in encoded
    assert encoded.count("[REDACTED]") >= 5
    assert "my-known-value" not in redact_text(
        "provider said my-known-value", known_secrets=("my-known-value",)
    )


def test_credential_resolver_supports_env_and_mounted_secrets_without_rendering_values(
    tmp_path: Path,
) -> None:
    mounted = tmp_path / "fixture-token"
    mounted.write_text("mounted-super-secret\n", encoding="utf-8")
    resolver = CredentialResolver(
        environment={
            "JARVIS_FIXTURE_TOKEN_FILE": str(mounted),
            "JARVIS_FIXTURE_OTHER": "environment-super-secret",
        },
        secret_root=tmp_path,
    )

    token = resolver.resolve("JARVIS_FIXTURE_TOKEN")
    other = resolver.resolve_provider("fixture", "other")

    assert token is not None and token.value.reveal() == "mounted-super-secret"
    assert other is not None and other.value.reveal() == "environment-super-secret"
    assert "mounted-super-secret" not in repr(token)
    assert "environment-super-secret" not in repr(other)
    assert token.as_dict() == {
        "name": "JARVIS_FIXTURE_TOKEN",
        "configured": True,
        "source": "mounted_secret",
    }


@pytest.mark.asyncio
async def test_receipt_store_commits_before_side_effect_and_binds_idempotency(
    tmp_path: Path,
) -> None:
    connector = FakeConnector()
    registry, store = make_registry(tmp_path, connector)
    observed_status: list[ReceiptStatus] = []

    async def inspect_receipt() -> None:
        receipt = await store.get_by_idempotency_key("durable-action")
        assert receipt is not None
        observed_status.append(receipt.status)

    connector.before_execute = inspect_receipt
    request = CapabilityRequest(
        capability_id="fixture.publish",
        payload={"body": "hello"},
        target={"channel": "one"},
        confirmed=True,
        idempotency_key="durable-action",
    )
    first = await registry.execute(request)
    duplicate = await registry.execute(
        CapabilityRequest(
            capability_id="fixture.publish",
            payload={"body": "hello"},
            target={"channel": "one"},
            confirmed=True,
            idempotency_key="durable-action",
        )
    )

    assert observed_status == [ReceiptStatus.STARTED]
    assert first.status is ExecutionStatus.VERIFIED
    assert first.success is True
    assert first.receipt.status is ReceiptStatus.VERIFIED
    assert duplicate.status is ExecutionStatus.VERIFIED
    assert len(connector.execute_calls) == 1

    with pytest.raises(IdempotencyConflict):
        await store.begin(
            request_id="different",
            conversation_id=None,
            capability_id="fixture.publish",
            provider_id="fixture",
            target={"channel": "two"},
            requested_operation="fixture.publish",
            request_payload={"body": "hello"},
            idempotency_key="durable-action",
        )

    with sqlite3.connect(tmp_path / "receipts.db") as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


@pytest.mark.asyncio
async def test_write_requires_confirmation_and_records_rejection(
    tmp_path: Path,
) -> None:
    connector = FakeConnector()
    registry, store = make_registry(tmp_path, connector)

    result = await registry.execute(
        "fixture.publish",
        {"body": "not authorized"},
        idempotency_key="needs-confirmation",
    )
    receipt = await store.get_by_idempotency_key("needs-confirmation")

    assert result.status is ExecutionStatus.REJECTED
    assert result.success is False
    assert connector.execute_calls == []
    assert receipt is not None and receipt.status is ReceiptStatus.REJECTED


@pytest.mark.asyncio
async def test_verified_write_receipt_and_response_recursively_redact_secrets(
    tmp_path: Path,
) -> None:
    connector = FakeConnector()
    connector.execute_results = [
        ConnectorResult.succeeded(
            {
                "message_id": "message-1",
                "access_token": "result-super-secret",
                "note": "Authorization: Bearer nested-secret",
            },
            provider_reference="message-1",
        )
    ]
    connector.verify_result = VerificationResult.verified(
        {"delivered": True, "password": "verification-secret"}
    )
    registry, _ = make_registry(tmp_path, connector)

    result = await registry.execute(
        "fixture.publish",
        {"body": "hello"},
        target={"recipient": "verified@example.test", "token": "target-secret"},
        confirmed=True,
        idempotency_key="verified-write",
    )
    encoded = json.dumps(result.as_dict())

    assert result.status is ExecutionStatus.VERIFIED
    assert result.receipt.status is ReceiptStatus.VERIFIED
    assert result.provider_reference == "message-1"
    for secret in (
        "result-super-secret",
        "nested-secret",
        "verification-secret",
        "target-secret",
    ):
        assert secret not in encoded


@pytest.mark.asyncio
async def test_provider_write_failure_is_terminal_failed_and_never_retried(
    tmp_path: Path,
) -> None:
    connector = FakeConnector()
    connector.execute_results = [
        ConnectorResult.failed("provider rejected write", retryable=True),
        ConnectorResult.succeeded({"should_not": "run"}),
    ]
    registry, _ = make_registry(tmp_path, connector, read_attempts=4)

    result = await registry.execute(
        "fixture.publish",
        {"body": "hello"},
        confirmed=True,
        idempotency_key="failed-write",
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.success is False
    assert result.receipt.status is ReceiptStatus.FAILED
    assert connector.execute_calls == ["fixture.publish"]


@pytest.mark.asyncio
async def test_write_exception_or_timeout_is_outcome_unknown_and_is_not_retried(
    tmp_path: Path,
) -> None:
    connector = FakeConnector()
    connector.execute_results = [RuntimeError("token=write-secret provider disconnected")]
    registry, _ = make_registry(tmp_path, connector)

    raised = await registry.execute(
        "fixture.publish",
        {"body": "hello"},
        confirmed=True,
        idempotency_key="unknown-write",
    )

    assert raised.status is ExecutionStatus.OUTCOME_UNKNOWN
    assert raised.success is False
    assert raised.receipt.status is ReceiptStatus.OUTCOME_UNKNOWN
    assert "write-secret" not in json.dumps(raised.as_dict())
    assert connector.execute_calls == ["fixture.publish"]

    timeout_connector = FakeConnector()
    timeout_connector.execute_delay = 0.2
    timeout_registry, _ = make_registry(tmp_path, timeout_connector)
    timed_out = await timeout_registry.execute(
        "fixture.publish",
        {"body": "hello"},
        confirmed=True,
        idempotency_key="timeout-write",
    )
    assert timed_out.status is ExecutionStatus.OUTCOME_UNKNOWN
    assert timed_out.receipt.status is ReceiptStatus.OUTCOME_UNKNOWN
    assert timeout_connector.execute_calls == ["fixture.publish"]


@pytest.mark.asyncio
async def test_accepted_unverified_write_does_not_claim_verified_success(
    tmp_path: Path,
) -> None:
    connector = FakeConnector()
    registry, _ = make_registry(tmp_path, connector)

    result = await registry.execute(
        "fixture.draft",
        {"body": "draft only"},
        standing_permission=True,
        idempotency_key="unverified-write",
    )

    assert result.status is ExecutionStatus.ACCEPTED_UNVERIFIED
    assert result.accepted is True
    assert result.success is False
    assert result.receipt.status is ReceiptStatus.ACCEPTED_UNVERIFIED


@pytest.mark.asyncio
async def test_read_failures_retry_only_when_retryable_and_never_fabricate_result(
    tmp_path: Path,
) -> None:
    connector = FakeConnector()
    connector.execute_results = [
        ConnectorResult.failed("temporary", retryable=True),
        ConnectorResult.succeeded({"items": ["real-provider-item"]}),
    ]
    registry, _ = make_registry(tmp_path, connector, read_attempts=2)

    recovered = await registry.execute("fixture.search", {"query": "item"})

    assert recovered.status is ExecutionStatus.SUCCEEDED
    assert recovered.success is True
    assert recovered.data == {"items": ["real-provider-item"]}
    assert recovered.attempts == 2

    connector.execute_results = [ConnectorResult.failed("permanent", retryable=False)]
    failed = await registry.execute("fixture.search", {"query": "missing"})
    assert failed.status is ExecutionStatus.FAILED
    assert failed.success is False
    assert failed.data == {}
    assert failed.attempts == 1


@pytest.mark.asyncio
async def test_unavailable_write_cannot_produce_success_or_call_provider(
    tmp_path: Path,
) -> None:
    connector = FakeConnector(configured=False, authenticated=False, healthy=False)
    registry, store = make_registry(tmp_path, connector)

    result = await registry.execute(
        "fixture.publish",
        {"body": "must not send"},
        confirmed=True,
        idempotency_key="unavailable-write",
    )
    receipt = await store.get_by_idempotency_key("unavailable-write")

    assert result.status is ExecutionStatus.UNAVAILABLE
    assert result.success is False
    assert result.accepted is False
    assert connector.execute_calls == []
    assert receipt is not None and receipt.status is ReceiptStatus.REJECTED


@pytest.mark.asyncio
async def test_write_is_blocked_when_durable_audit_is_not_configured() -> None:
    connector = FakeConnector()
    registry = ConnectorRegistry(receipt_store=None)
    registry.register(connector)

    result = await registry.execute(
        "fixture.publish",
        {"body": "must not send"},
        confirmed=True,
    )

    assert result.status is ExecutionStatus.REJECTED
    assert result.success is False
    assert connector.execute_calls == []
