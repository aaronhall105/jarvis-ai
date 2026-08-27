"""Capability discovery, health, policy, execution, and verification boundary."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.connectors.audit import (
    ActionReceipt,
    ActionReceiptStore,
    IdempotencyConflict,
    ReceiptClaim,
    ReceiptStatus,
)
from app.connectors.base import (
    CapabilityExecution,
    CapabilityMetadata,
    CapabilityRequest,
    ConfirmationMode,
    Connector,
    ConnectorResult,
    ExecutionStatus,
    ProviderResultStatus,
    ProviderStatus,
    VerificationMode,
    VerificationResult,
    VerificationStatus,
)
from app.connectors.credentials import redact_secrets, redact_text


class ConnectorRegistrationError(ValueError):
    """Connector or capability identifiers collide or are inconsistent."""


@dataclass(frozen=True, slots=True)
class _CachedStatus:
    status: ProviderStatus
    expires_at: float


_RECEIPT_TO_EXECUTION = {
    ReceiptStatus.VERIFIED: ExecutionStatus.VERIFIED,
    ReceiptStatus.ACCEPTED_UNVERIFIED: ExecutionStatus.ACCEPTED_UNVERIFIED,
    ReceiptStatus.FAILED: ExecutionStatus.FAILED,
    ReceiptStatus.OUTCOME_UNKNOWN: ExecutionStatus.OUTCOME_UNKNOWN,
    ReceiptStatus.REJECTED: ExecutionStatus.REJECTED,
}


class ConnectorRegistry:
    """Provider-neutral registry and the sole external execution boundary."""

    def __init__(
        self,
        *,
        receipt_store: ActionReceiptStore | None = None,
        health_ttl_seconds: float = 30.0,
        health_timeout_seconds: float = 5.0,
        read_attempts: int = 2,
        read_retry_delay_seconds: float = 0.05,
    ) -> None:
        self.receipt_store = receipt_store
        self.health_ttl_seconds = max(0.0, min(float(health_ttl_seconds), 300.0))
        self.health_timeout_seconds = max(0.05, min(float(health_timeout_seconds), 30.0))
        self.read_attempts = max(1, min(int(read_attempts), 4))
        self.read_retry_delay_seconds = max(0.0, min(float(read_retry_delay_seconds), 2.0))
        self._connectors: dict[str, Connector] = {}
        self._capabilities: dict[str, CapabilityMetadata] = {}
        self._capability_providers: dict[str, str] = {}
        self._status_cache: dict[str, _CachedStatus] = {}
        self._health_locks: dict[str, asyncio.Lock] = {}

    def register(self, connector: Connector) -> Connector:
        if connector.provider_id in self._connectors:
            raise ConnectorRegistrationError(
                f"Provider is already registered: {connector.provider_id}"
            )
        collisions = [
            item.capability_id
            for item in connector.capabilities
            if item.capability_id in self._capabilities
        ]
        if collisions:
            raise ConnectorRegistrationError(f"Capability is already registered: {min(collisions)}")
        self._connectors[connector.provider_id] = connector
        self._health_locks[connector.provider_id] = asyncio.Lock()
        for item in connector.capabilities:
            self._capabilities[item.capability_id] = item
            self._capability_providers[item.capability_id] = connector.provider_id
        self._status_cache.pop(connector.provider_id, None)
        return connector

    def unregister(self, provider_id: str) -> Connector | None:
        connector = self._connectors.pop(provider_id, None)
        if connector is None:
            return None
        for item in connector.capabilities:
            self._capabilities.pop(item.capability_id, None)
            self._capability_providers.pop(item.capability_id, None)
        self._status_cache.pop(provider_id, None)
        self._health_locks.pop(provider_id, None)
        return connector

    @property
    def provider_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._connectors))

    def capability_definition(self, capability_id: str) -> CapabilityMetadata | None:
        """Return an installed definition, not a claim that it is executable."""

        return self._capabilities.get(capability_id)

    def potential_capabilities(self) -> tuple[CapabilityMetadata, ...]:
        return tuple(self._capabilities[key] for key in sorted(self._capabilities))

    @staticmethod
    def _health_failure(
        connector: Connector,
        previous: ProviderStatus | None,
        reason: str,
    ) -> ProviderStatus:
        return ProviderStatus(
            provider_id=connector.provider_id,
            name=connector.name,
            configured=previous.configured if previous else False,
            authenticated=previous.authenticated if previous else False,
            healthy=False,
            health_reason=reason,
            setup_requirements=(
                previous.setup_requirements
                if previous
                else ("Check provider configuration, credentials, and connectivity",)
            ),
            scopes=previous.scopes if previous else frozenset(),
            potential_capabilities=tuple(item.capability_id for item in connector.capabilities),
            executable_capabilities=(),
            checked_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _normalise_status(connector: Connector, observed: ProviderStatus) -> ProviderStatus:
        known = {item.capability_id: item for item in connector.capabilities}
        configured = bool(observed.configured)
        authenticated = configured and bool(observed.authenticated)
        healthy = configured and authenticated and bool(observed.healthy)
        scopes = frozenset(observed.scopes)
        reason: str | None

        if not configured:
            reason = observed.health_reason or "No provider configured"
        elif not authenticated:
            reason = observed.health_reason or "Provider authentication is not valid"
        elif not healthy:
            reason = observed.health_reason or "Provider health check failed"
        else:
            reason = observed.health_reason

        if not healthy:
            executable: tuple[str, ...] = ()
        else:
            declared = (
                tuple(known)
                if observed.executable_capabilities is None
                else observed.executable_capabilities
            )
            executable = tuple(
                capability_id
                for capability_id in dict.fromkeys(declared)
                if capability_id in known and known[capability_id].required_scopes.issubset(scopes)
            )

        safe_requirements = tuple(
            redact_text(item, max_length=500) for item in observed.setup_requirements
        )
        return ProviderStatus(
            provider_id=connector.provider_id,
            name=connector.name,
            configured=configured,
            authenticated=authenticated,
            healthy=healthy,
            health_reason=redact_text(reason, max_length=1_000) if reason else None,
            setup_requirements=safe_requirements,
            scopes=frozenset(redact_text(scope, max_length=200) for scope in scopes),
            potential_capabilities=tuple(sorted(known)),
            executable_capabilities=tuple(sorted(executable)),
            checked_at=observed.checked_at,
        )

    async def provider_status(
        self,
        provider_id: str,
        *,
        refresh: bool = False,
    ) -> ProviderStatus:
        connector = self._connectors.get(provider_id)
        if connector is None:
            raise KeyError(f"Unknown connector provider: {provider_id}")
        now = time.monotonic()
        cached = self._status_cache.get(provider_id)
        if not refresh and cached is not None and cached.expires_at > now:
            return cached.status

        lock = self._health_locks[provider_id]
        async with lock:
            now = time.monotonic()
            cached = self._status_cache.get(provider_id)
            if not refresh and cached is not None and cached.expires_at > now:
                return cached.status
            previous = cached.status if cached is not None else None
            try:
                if refresh:
                    invalidate = getattr(connector, "invalidate_health_cache", None)
                    if callable(invalidate):
                        invalidate()
                observed = await asyncio.wait_for(
                    connector.status(), timeout=self.health_timeout_seconds
                )
                if not isinstance(observed, ProviderStatus):
                    raise TypeError("Connector status returned an invalid result")
                status = self._normalise_status(connector, observed)
            except TimeoutError:
                status = self._health_failure(
                    connector, previous, "Provider health check timed out"
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                safe = redact_text(exc, max_length=500)
                status = self._health_failure(
                    connector,
                    previous,
                    f"Provider health check failed: {safe}",
                )
            self._status_cache[provider_id] = _CachedStatus(
                status,
                time.monotonic() + self.health_ttl_seconds,
            )
            return status

    async def status_snapshot(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        statuses = await asyncio.gather(
            *(
                self.provider_status(provider_id, refresh=refresh)
                for provider_id in self.provider_ids
            )
        )
        return [status.as_dict() for status in statuses]

    async def health_snapshot(self, *, refresh: bool = False) -> dict[str, Any]:
        providers = await self.status_snapshot(refresh=refresh)
        configured = [item for item in providers if item["configured"]]
        return {
            "healthy": all(item["healthy"] for item in configured),
            "provider_count": len(providers),
            "configured_provider_count": len(configured),
            "available_provider_count": sum(bool(item["available"]) for item in providers),
            "providers": providers,
        }

    async def get_capability(
        self,
        capability_id: str,
        *,
        refresh: bool = False,
    ) -> CapabilityMetadata | None:
        metadata = self._capabilities.get(capability_id)
        if metadata is None:
            return None
        status = await self.provider_status(metadata.provider_id, refresh=refresh)
        if capability_id not in (status.executable_capabilities or ()):
            return None
        return metadata

    async def executable_capabilities(
        self,
        *,
        refresh: bool = False,
    ) -> tuple[CapabilityMetadata, ...]:
        statuses = await asyncio.gather(
            *(
                self.provider_status(provider_id, refresh=refresh)
                for provider_id in self.provider_ids
            )
        )
        executable = {
            capability_id
            for status in statuses
            for capability_id in (status.executable_capabilities or ())
        }
        return tuple(
            self._capabilities[capability_id]
            for capability_id in sorted(executable)
            if capability_id in self._capabilities
        )

    async def capability_snapshot(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        statuses = {
            status.provider_id: status
            for status in await asyncio.gather(
                *(
                    self.provider_status(provider_id, refresh=refresh)
                    for provider_id in self.provider_ids
                )
            )
        }
        output: list[dict[str, Any]] = []
        for capability in self.potential_capabilities():
            item = capability.as_dict()
            status = statuses[capability.provider_id]
            item["available"] = capability.capability_id in (status.executable_capabilities or ())
            item["unavailable_reason"] = None if item["available"] else status.health_reason
            output.append(item)
        return output

    @staticmethod
    def _execution_from_receipt(
        request: CapabilityRequest,
        metadata: CapabilityMetadata,
        receipt: ActionReceipt,
    ) -> CapabilityExecution:
        error: str | None
        if receipt.status is ReceiptStatus.STARTED:
            status = ExecutionStatus.OUTCOME_UNKNOWN
            error = "An action with this idempotency key is already in progress"
        else:
            status = _RECEIPT_TO_EXECUTION[receipt.status]
            error = receipt.error
        return CapabilityExecution(
            request_id=request.request_id,
            capability_id=metadata.capability_id,
            provider_id=metadata.provider_id,
            status=status,
            data=receipt.result,
            error=error,
            provider_reference=receipt.provider_reference,
            verification=receipt.verification,
            receipt=receipt,
            attempts=0,
        )

    async def _claim_write(
        self,
        metadata: CapabilityMetadata,
        request: CapabilityRequest,
    ) -> ReceiptClaim | CapabilityExecution:
        if self.receipt_store is None:
            return CapabilityExecution(
                request_id=request.request_id,
                capability_id=metadata.capability_id,
                provider_id=metadata.provider_id,
                status=ExecutionStatus.REJECTED,
                error="External writes are disabled because action auditing is not configured",
            )
        try:
            claim = await self.receipt_store.begin(
                request_id=request.request_id,
                conversation_id=request.conversation_id,
                capability_id=metadata.capability_id,
                provider_id=metadata.provider_id,
                target=request.target,
                requested_operation=request.operation or metadata.capability_id,
                request_payload=request.payload,
                idempotency_key=request.idempotency_key or request.request_id,
            )
        except IdempotencyConflict as exc:
            return CapabilityExecution(
                request_id=request.request_id,
                capability_id=metadata.capability_id,
                provider_id=metadata.provider_id,
                status=ExecutionStatus.REJECTED,
                error=redact_text(exc, max_length=1_000),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return CapabilityExecution(
                request_id=request.request_id,
                capability_id=metadata.capability_id,
                provider_id=metadata.provider_id,
                status=ExecutionStatus.REJECTED,
                error=f"Action auditing is unavailable: {redact_text(exc, max_length=500)}",
            )
        if not claim.claimed:
            return self._execution_from_receipt(request, metadata, claim.receipt)
        return claim

    async def _finish_write(
        self,
        *,
        request: CapabilityRequest,
        metadata: CapabilityMetadata,
        receipt: ActionReceipt,
        execution_status: ExecutionStatus,
        receipt_status: ReceiptStatus,
        data: Mapping[str, Any] | None = None,
        error: str | None = None,
        provider_reference: str | None = None,
        verification: Mapping[str, Any] | None = None,
        attempts: int = 0,
        side_effect_started: bool = False,
    ) -> CapabilityExecution:
        safe_data = redact_secrets(data or {})
        safe_verification = redact_secrets(verification or {})
        safe_error = redact_text(error, max_length=2_000) if error else None
        safe_reference = (
            redact_text(provider_reference, max_length=1_000) if provider_reference else None
        )
        try:
            if self.receipt_store is None:  # pragma: no cover - guarded by claim
                raise RuntimeError("Action receipt store is unavailable")
            completed = await self.receipt_store.complete(
                receipt.action_id,
                status=receipt_status,
                provider_reference=safe_reference,
                result=safe_data,
                verification=safe_verification,
                error=safe_error,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            audit_error = redact_text(exc, max_length=500)
            if side_effect_started:
                execution_status = ExecutionStatus.OUTCOME_UNKNOWN
                safe_error = (
                    "The provider was called, but its outcome could not be durably recorded: "
                    f"{audit_error}"
                )
            else:
                safe_error = (
                    f"The rejected action could not be finalized in the audit: {audit_error}"
                )
            completed = receipt
        return CapabilityExecution(
            request_id=request.request_id,
            capability_id=metadata.capability_id,
            provider_id=metadata.provider_id,
            status=execution_status,
            data=safe_data,
            error=safe_error,
            provider_reference=safe_reference,
            verification=safe_verification,
            receipt=completed,
            attempts=attempts,
        )

    async def _reject_write(
        self,
        request: CapabilityRequest,
        metadata: CapabilityMetadata,
        receipt: ActionReceipt,
        *,
        status: ExecutionStatus,
        error: str,
    ) -> CapabilityExecution:
        return await self._finish_write(
            request=request,
            metadata=metadata,
            receipt=receipt,
            execution_status=status,
            receipt_status=ReceiptStatus.REJECTED,
            error=error,
        )

    @staticmethod
    def _approval_error(
        metadata: CapabilityMetadata,
        request: CapabilityRequest,
    ) -> str | None:
        if metadata.confirmation is ConfirmationMode.REQUIRED and not request.confirmed:
            return "Explicit confirmation is required for this capability"
        if (
            metadata.confirmation is ConfirmationMode.CONTEXTUAL
            and not request.confirmed
            and not request.standing_permission
        ):
            return "Confirmation or an explicit scoped standing permission is required"
        return None

    async def _execute_read(
        self,
        connector: Connector,
        metadata: CapabilityMetadata,
        request: CapabilityRequest,
    ) -> CapabilityExecution:
        last_error = "Provider execution failed"
        for attempt in range(1, self.read_attempts + 1):
            try:
                result = await asyncio.wait_for(
                    connector.execute(metadata, request),
                    timeout=metadata.timeout_seconds,
                )
                if not isinstance(result, ConnectorResult):
                    raise TypeError("Connector returned an invalid execution result")
            except TimeoutError:
                result = None
                last_error = "Provider read timed out"
                retryable = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                result = None
                last_error = f"Provider read failed: {redact_text(exc, max_length=500)}"
                retryable = True
            else:
                assert result is not None
                retryable = result.retryable
                last_error = (
                    redact_text(result.error, max_length=2_000) if result.error else last_error
                )
                if result.status is ProviderResultStatus.SUCCEEDED:
                    return CapabilityExecution(
                        request_id=request.request_id,
                        capability_id=metadata.capability_id,
                        provider_id=metadata.provider_id,
                        status=ExecutionStatus.SUCCEEDED,
                        data=redact_secrets(result.data),
                        provider_reference=(
                            redact_text(result.provider_reference, max_length=1_000)
                            if result.provider_reference
                            else None
                        ),
                        attempts=attempt,
                    )
                if result.status is ProviderResultStatus.OUTCOME_UNKNOWN:
                    return CapabilityExecution(
                        request_id=request.request_id,
                        capability_id=metadata.capability_id,
                        provider_id=metadata.provider_id,
                        status=ExecutionStatus.OUTCOME_UNKNOWN,
                        error=last_error,
                        attempts=attempt,
                    )
            if attempt >= self.read_attempts or not retryable:
                return CapabilityExecution(
                    request_id=request.request_id,
                    capability_id=metadata.capability_id,
                    provider_id=metadata.provider_id,
                    status=ExecutionStatus.FAILED,
                    error=last_error,
                    attempts=attempt,
                )
            if self.read_retry_delay_seconds:
                await asyncio.sleep(self.read_retry_delay_seconds * attempt)

        raise AssertionError("unreachable read attempt loop")  # pragma: no cover

    async def _execute_write(
        self,
        connector: Connector,
        metadata: CapabilityMetadata,
        request: CapabilityRequest,
        receipt: ActionReceipt,
    ) -> CapabilityExecution:
        try:
            result = await asyncio.wait_for(
                connector.execute(metadata, request),
                timeout=metadata.timeout_seconds,
            )
            if not isinstance(result, ConnectorResult):
                raise TypeError("Connector returned an invalid execution result")
        except asyncio.CancelledError:
            if self.receipt_store is not None:
                await asyncio.shield(
                    self.receipt_store.complete(
                        receipt.action_id,
                        status=ReceiptStatus.OUTCOME_UNKNOWN,
                        error="Execution was cancelled after the provider call began",
                    )
                )
            raise
        except TimeoutError:
            return await self._finish_write(
                request=request,
                metadata=metadata,
                receipt=receipt,
                execution_status=ExecutionStatus.OUTCOME_UNKNOWN,
                receipt_status=ReceiptStatus.OUTCOME_UNKNOWN,
                error="Provider write timed out; the external outcome is unknown",
                attempts=1,
                side_effect_started=True,
            )
        except Exception as exc:
            return await self._finish_write(
                request=request,
                metadata=metadata,
                receipt=receipt,
                execution_status=ExecutionStatus.OUTCOME_UNKNOWN,
                receipt_status=ReceiptStatus.OUTCOME_UNKNOWN,
                error=(
                    "Provider write raised an error after execution began; the outcome is unknown: "
                    f"{redact_text(exc, max_length=500)}"
                ),
                attempts=1,
                side_effect_started=True,
            )

        safe_data = redact_secrets(result.data)
        safe_error = redact_text(result.error, max_length=2_000) if result.error else None
        safe_reference = (
            redact_text(result.provider_reference, max_length=1_000)
            if result.provider_reference
            else None
        )
        if result.status is ProviderResultStatus.FAILED:
            return await self._finish_write(
                request=request,
                metadata=metadata,
                receipt=receipt,
                execution_status=ExecutionStatus.FAILED,
                receipt_status=ReceiptStatus.FAILED,
                data=safe_data,
                error=safe_error or "Provider rejected the write",
                provider_reference=safe_reference,
                attempts=1,
                side_effect_started=True,
            )
        if result.status is ProviderResultStatus.OUTCOME_UNKNOWN:
            return await self._finish_write(
                request=request,
                metadata=metadata,
                receipt=receipt,
                execution_status=ExecutionStatus.OUTCOME_UNKNOWN,
                receipt_status=ReceiptStatus.OUTCOME_UNKNOWN,
                data=safe_data,
                error=safe_error or "Provider could not determine the write outcome",
                provider_reference=safe_reference,
                attempts=1,
                side_effect_started=True,
            )

        if metadata.verification is VerificationMode.NONE:
            return await self._finish_write(
                request=request,
                metadata=metadata,
                receipt=receipt,
                execution_status=ExecutionStatus.ACCEPTED_UNVERIFIED,
                receipt_status=ReceiptStatus.ACCEPTED_UNVERIFIED,
                data=safe_data,
                provider_reference=safe_reference,
                attempts=1,
                side_effect_started=True,
            )

        verification: VerificationResult | None = None
        verification_error: str | None = None
        try:
            verification = await asyncio.wait_for(
                connector.verify(metadata, request, result),
                timeout=metadata.timeout_seconds,
            )
            if not isinstance(verification, VerificationResult):
                raise TypeError("Connector returned an invalid verification result")
        except asyncio.CancelledError:
            if self.receipt_store is not None:
                await asyncio.shield(
                    self.receipt_store.complete(
                        receipt.action_id,
                        status=ReceiptStatus.ACCEPTED_UNVERIFIED,
                        provider_reference=safe_reference,
                        result=safe_data,
                        error="Verification was cancelled after the provider accepted the write",
                    )
                )
            raise
        except TimeoutError:
            verification_error = "Verification timed out after the provider accepted the write"
        except Exception as exc:
            verification_error = (
                "Verification failed after the provider accepted the write: "
                f"{redact_text(exc, max_length=500)}"
            )

        if verification is not None and verification.status is VerificationStatus.VERIFIED:
            evidence = redact_secrets(verification.evidence)
            return await self._finish_write(
                request=request,
                metadata=metadata,
                receipt=receipt,
                execution_status=ExecutionStatus.VERIFIED,
                receipt_status=ReceiptStatus.VERIFIED,
                data=safe_data,
                provider_reference=safe_reference,
                verification=evidence,
                attempts=1,
                side_effect_started=True,
            )

        evidence = redact_secrets(verification.evidence) if verification is not None else {}
        if verification is not None and verification.error:
            verification_error = redact_text(verification.error, max_length=1_000)
        return await self._finish_write(
            request=request,
            metadata=metadata,
            receipt=receipt,
            execution_status=ExecutionStatus.ACCEPTED_UNVERIFIED,
            receipt_status=ReceiptStatus.ACCEPTED_UNVERIFIED,
            data=safe_data,
            error=verification_error,
            provider_reference=safe_reference,
            verification=evidence,
            attempts=1,
            side_effect_started=True,
        )

    async def execute(
        self,
        request: CapabilityRequest | str,
        payload: Mapping[str, Any] | None = None,
        *,
        request_id: str | None = None,
        conversation_id: str | None = None,
        principal_id: str | None = None,
        target: Any = None,
        operation: str | None = None,
        confirmed: bool = False,
        standing_permission: bool = False,
        allowed_scopes: frozenset[str] | set[str] | None = None,
        idempotency_key: str | None = None,
        refresh_health: bool = False,
    ) -> CapabilityExecution:
        if isinstance(request, str):
            request = CapabilityRequest(
                capability_id=request,
                payload=payload or {},
                request_id=request_id or str(uuid.uuid4()),
                conversation_id=conversation_id,
                principal_id=principal_id,
                target=target,
                operation=operation,
                confirmed=confirmed,
                standing_permission=standing_permission,
                allowed_scopes=(frozenset(allowed_scopes) if allowed_scopes is not None else None),
                idempotency_key=idempotency_key,
            )
        elif payload is not None:
            raise TypeError(
                "payload must be included in CapabilityRequest when request is an object"
            )

        metadata = self._capabilities.get(request.capability_id)
        if metadata is None:
            return CapabilityExecution(
                request_id=request.request_id,
                capability_id=request.capability_id,
                provider_id=None,
                status=ExecutionStatus.UNAVAILABLE,
                error="No executable connector provides this capability",
            )
        connector = self._connectors[metadata.provider_id]

        receipt: ActionReceipt | None = None
        if metadata.is_write:
            claimed = await self._claim_write(metadata, request)
            if isinstance(claimed, CapabilityExecution):
                return claimed
            receipt = claimed.receipt

        status = await self.provider_status(metadata.provider_id, refresh=refresh_health)
        if not status.available:
            error = status.health_reason or "Provider is unavailable"
            if receipt is not None:
                return await self._reject_write(
                    request,
                    metadata,
                    receipt,
                    status=ExecutionStatus.UNAVAILABLE,
                    error=error,
                )
            return CapabilityExecution(
                request_id=request.request_id,
                capability_id=metadata.capability_id,
                provider_id=metadata.provider_id,
                status=ExecutionStatus.UNAVAILABLE,
                error=error,
            )

        missing_provider_scopes = metadata.required_scopes - status.scopes
        if missing_provider_scopes:
            error = "Provider authorization is missing required scopes: " + ", ".join(
                sorted(missing_provider_scopes)
            )
            if receipt is not None:
                return await self._reject_write(
                    request,
                    metadata,
                    receipt,
                    status=ExecutionStatus.UNAVAILABLE,
                    error=error,
                )
            return CapabilityExecution(
                request_id=request.request_id,
                capability_id=metadata.capability_id,
                provider_id=metadata.provider_id,
                status=ExecutionStatus.UNAVAILABLE,
                error=error,
            )

        if request.allowed_scopes is not None:
            missing_policy_scopes = metadata.required_scopes - request.allowed_scopes
            if missing_policy_scopes:
                error = "Caller policy does not grant required scopes: " + ", ".join(
                    sorted(missing_policy_scopes)
                )
                if receipt is not None:
                    return await self._reject_write(
                        request,
                        metadata,
                        receipt,
                        status=ExecutionStatus.REJECTED,
                        error=error,
                    )
                return CapabilityExecution(
                    request_id=request.request_id,
                    capability_id=metadata.capability_id,
                    provider_id=metadata.provider_id,
                    status=ExecutionStatus.REJECTED,
                    error=error,
                )

        approval_error = self._approval_error(metadata, request)
        if approval_error:
            if receipt is not None:
                return await self._reject_write(
                    request,
                    metadata,
                    receipt,
                    status=ExecutionStatus.REJECTED,
                    error=approval_error,
                )
            return CapabilityExecution(
                request_id=request.request_id,
                capability_id=metadata.capability_id,
                provider_id=metadata.provider_id,
                status=ExecutionStatus.REJECTED,
                error=approval_error,
            )

        if metadata.capability_id not in (status.executable_capabilities or ()):
            error = "Provider does not currently expose this capability"
            if receipt is not None:
                return await self._reject_write(
                    request,
                    metadata,
                    receipt,
                    status=ExecutionStatus.UNAVAILABLE,
                    error=error,
                )
            return CapabilityExecution(
                request_id=request.request_id,
                capability_id=metadata.capability_id,
                provider_id=metadata.provider_id,
                status=ExecutionStatus.UNAVAILABLE,
                error=error,
            )

        if metadata.is_write:
            if receipt is None:  # pragma: no cover - guarded above
                raise AssertionError("write execution lacks a durable receipt")
            return await self._execute_write(connector, metadata, request, receipt)
        return await self._execute_read(connector, metadata, request)
