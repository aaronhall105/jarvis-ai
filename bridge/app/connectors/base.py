"""Provider-neutral contracts for Jarvis external connectors.

Connectors describe what they *could* do separately from their live status.  The
registry is the only component that turns those definitions into executable
capabilities.  Keeping that distinction explicit prevents an installed adapter
from being advertised when its account, credentials, scopes, or health are not
usable.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class CapabilityAccess(str, Enum):
    READ = "read"
    WRITE = "write"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ConfirmationMode(str, Enum):
    """How policy must authorize a capability before connector execution."""

    NONE = "none"
    CONTEXTUAL = "contextual"
    REQUIRED = "required"


class VerificationMode(str, Enum):
    """The verification contract offered by a connector capability."""

    NONE = "none"
    OPTIONAL = "optional"
    REQUIRED = "required"


class ProviderResultStatus(str, Enum):
    """What an adapter knows immediately after calling its provider."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    OUTCOME_UNKNOWN = "outcome_unknown"


class ExecutionStatus(str, Enum):
    """Registry-level result states exposed to planners and conversations."""

    SUCCEEDED = "succeeded"  # A read completed and returned provider evidence.
    VERIFIED = "verified"  # A write completed and was independently verified.
    ACCEPTED_UNVERIFIED = "accepted_unverified"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class CapabilityMetadata:
    """Immutable, provider-neutral capability definition."""

    capability_id: str
    provider_id: str
    name: str
    description: str = ""
    access: CapabilityAccess = CapabilityAccess.READ
    required_scopes: frozenset[str] = field(default_factory=frozenset)
    risk: RiskLevel = RiskLevel.LOW
    confirmation: ConfirmationMode = ConfirmationMode.NONE
    supports_async: bool = False
    verification: VerificationMode = VerificationMode.NONE
    timeout_seconds: float = 15.0
    repeatable: bool = False
    minimum_poll_interval_seconds: int | None = None
    maximum_monitor_polls: int | None = None
    monitor_ttl_seconds: int | None = None
    monitor_value_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        capability_id = str(self.capability_id or "").strip()
        provider_id = str(self.provider_id or "").strip()
        name = str(self.name or "").strip()
        if not capability_id or "." not in capability_id:
            raise ValueError("capability_id must be a non-empty dotted identifier")
        if not provider_id:
            raise ValueError("provider_id must not be empty")
        if not name:
            raise ValueError("capability name must not be empty")
        timeout = float(self.timeout_seconds)
        if timeout <= 0 or timeout > 300:
            raise ValueError("timeout_seconds must be greater than zero and at most 300")
        monitor_fields = (
            self.minimum_poll_interval_seconds,
            self.maximum_monitor_polls,
            self.monitor_ttl_seconds,
        )
        if self.repeatable:
            if self.access is not CapabilityAccess.READ:
                raise ValueError("Only read capabilities may be repeatable monitors")
            if any(value is None for value in monitor_fields):
                raise ValueError(
                    "Repeatable capabilities require polling interval, poll limit, "
                    "and monitor TTL policies"
                )
            minimum = int(self.minimum_poll_interval_seconds or 0)
            maximum = int(self.maximum_monitor_polls or 0)
            ttl = int(self.monitor_ttl_seconds or 0)
            if minimum < 10 or minimum > 30 * 86400:
                raise ValueError("Monitor minimum polling interval is out of range")
            if maximum < 1 or maximum > 10_000:
                raise ValueError("Monitor poll limit is out of range")
            if ttl < minimum or ttl > 365 * 86400:
                raise ValueError("Monitor TTL is out of range")
            paths = tuple(
                dict.fromkeys(
                    str(path).strip() for path in self.monitor_value_paths if str(path).strip()
                )
            )
            if not paths:
                raise ValueError("Repeatable capabilities require at least one stable value path")
            object.__setattr__(self, "minimum_poll_interval_seconds", minimum)
            object.__setattr__(self, "maximum_monitor_polls", maximum)
            object.__setattr__(self, "monitor_ttl_seconds", ttl)
            object.__setattr__(self, "monitor_value_paths", paths)
        elif any(value is not None for value in monitor_fields):
            raise ValueError("Monitor policies require repeatable=True")
        elif self.monitor_value_paths:
            raise ValueError("Monitor value paths require repeatable=True")

        object.__setattr__(self, "capability_id", capability_id)
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "name", name)
        object.__setattr__(
            self,
            "required_scopes",
            frozenset(str(scope).strip() for scope in self.required_scopes if str(scope).strip()),
        )
        object.__setattr__(self, "timeout_seconds", timeout)

    @property
    def is_write(self) -> bool:
        return self.access is CapabilityAccess.WRITE

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "provider_id": self.provider_id,
            "name": self.name,
            "description": self.description,
            "access": self.access.value,
            "required_scopes": sorted(self.required_scopes),
            "risk": self.risk.value,
            "confirmation": self.confirmation.value,
            "supports_async": self.supports_async,
            "verification": self.verification.value,
            "timeout_seconds": self.timeout_seconds,
            "repeatable": self.repeatable,
            "minimum_poll_interval_seconds": self.minimum_poll_interval_seconds,
            "maximum_monitor_polls": self.maximum_monitor_polls,
            "monitor_ttl_seconds": self.monitor_ttl_seconds,
            "monitor_value_paths": list(self.monitor_value_paths),
        }


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    """A connector's redaction-safe account and health observation.

    ``executable_capabilities=None`` means every registered capability may be
    executable when the provider is otherwise available.  An explicit empty
    tuple means that the account currently exposes none (for example because
    provider-specific feature access is absent).
    """

    provider_id: str
    name: str
    configured: bool
    authenticated: bool
    healthy: bool
    health_reason: str | None = None
    setup_requirements: tuple[str, ...] = ()
    scopes: frozenset[str] = field(default_factory=frozenset)
    potential_capabilities: tuple[str, ...] = ()
    executable_capabilities: tuple[str, ...] | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "setup_requirements",
            tuple(str(item).strip() for item in self.setup_requirements if str(item).strip()),
        )
        object.__setattr__(
            self,
            "scopes",
            frozenset(str(scope).strip() for scope in self.scopes if str(scope).strip()),
        )
        object.__setattr__(
            self,
            "potential_capabilities",
            tuple(dict.fromkeys(str(item) for item in self.potential_capabilities if str(item))),
        )
        if self.executable_capabilities is not None:
            object.__setattr__(
                self,
                "executable_capabilities",
                tuple(
                    dict.fromkeys(str(item) for item in self.executable_capabilities if str(item))
                ),
            )
        checked_at = self.checked_at
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
            object.__setattr__(self, "checked_at", checked_at)

    @property
    def available(self) -> bool:
        return self.configured and self.authenticated and self.healthy

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "name": self.name,
            "configured": self.configured,
            "authenticated": self.authenticated,
            "healthy": self.healthy,
            "available": self.available,
            "health_reason": self.health_reason,
            "setup_requirements": list(self.setup_requirements),
            "scopes": sorted(self.scopes),
            "potential_capabilities": list(self.potential_capabilities),
            "executable_capabilities": list(self.executable_capabilities or ()),
            "checked_at": self.checked_at.astimezone(timezone.utc).isoformat(),
        }


@dataclass(frozen=True, slots=True)
class CapabilityRequest:
    capability_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    conversation_id: str | None = None
    principal_id: str | None = None
    target: Any = None
    operation: str | None = None
    confirmed: bool = False
    standing_permission: bool = False
    allowed_scopes: frozenset[str] | None = None
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        if not str(self.capability_id or "").strip():
            raise ValueError("capability_id must not be empty")
        if not str(self.request_id or "").strip():
            raise ValueError("request_id must not be empty")
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")
        if self.allowed_scopes is not None:
            object.__setattr__(
                self,
                "allowed_scopes",
                frozenset(
                    str(scope).strip() for scope in self.allowed_scopes if str(scope).strip()
                ),
            )


@dataclass(frozen=True, slots=True)
class ConnectorResult:
    status: ProviderResultStatus
    data: Mapping[str, Any] = field(default_factory=dict)
    provider_reference: str | None = None
    error: str | None = None
    retryable: bool = False

    @classmethod
    def succeeded(
        cls,
        data: Mapping[str, Any] | None = None,
        *,
        provider_reference: str | None = None,
    ) -> ConnectorResult:
        return cls(
            ProviderResultStatus.SUCCEEDED,
            data or {},
            provider_reference=provider_reference,
        )

    @classmethod
    def failed(cls, error: str, *, retryable: bool = False) -> ConnectorResult:
        return cls(ProviderResultStatus.FAILED, error=error, retryable=retryable)

    @classmethod
    def outcome_unknown(cls, error: str) -> ConnectorResult:
        return cls(ProviderResultStatus.OUTCOME_UNKNOWN, error=error)


@dataclass(frozen=True, slots=True)
class VerificationResult:
    status: VerificationStatus
    evidence: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None

    @classmethod
    def verified(cls, evidence: Mapping[str, Any] | None = None) -> VerificationResult:
        return cls(VerificationStatus.VERIFIED, evidence or {})

    @classmethod
    def unverified(cls, error: str | None = None) -> VerificationResult:
        return cls(VerificationStatus.UNVERIFIED, error=error)


@dataclass(frozen=True, slots=True)
class CapabilityExecution:
    request_id: str
    capability_id: str
    provider_id: str | None
    status: ExecutionStatus
    data: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None
    provider_reference: str | None = None
    verification: Mapping[str, Any] = field(default_factory=dict)
    receipt: Any | None = None
    attempts: int = 0

    @property
    def success(self) -> bool:
        """True only for an evidenced read or a verified external write."""

        return self.status in {ExecutionStatus.SUCCEEDED, ExecutionStatus.VERIFIED}

    @property
    def accepted(self) -> bool:
        return self.status in {
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.VERIFIED,
            ExecutionStatus.ACCEPTED_UNVERIFIED,
        }

    def as_dict(self) -> dict[str, Any]:
        receipt = self.receipt
        receipt_value = receipt
        serializer = getattr(receipt, "as_dict", None)
        if callable(serializer):
            receipt_value = serializer()
        return {
            "request_id": self.request_id,
            "capability_id": self.capability_id,
            "provider_id": self.provider_id,
            "status": self.status.value,
            "success": self.success,
            "accepted": self.accepted,
            "data": dict(self.data),
            "error": self.error,
            "provider_reference": self.provider_reference,
            "verification": dict(self.verification),
            "receipt": receipt_value,
            "attempts": self.attempts,
        }


class Connector(ABC):
    """Base interface implemented by every real or test provider adapter."""

    def __init__(
        self,
        *,
        provider_id: str,
        name: str,
        capabilities: tuple[CapabilityMetadata, ...] | list[CapabilityMetadata],
    ) -> None:
        self.provider_id = str(provider_id or "").strip()
        self.name = str(name or "").strip()
        if not self.provider_id or not self.name:
            raise ValueError("connector provider_id and name must not be empty")
        items = tuple(capabilities)
        if not items:
            raise ValueError("connector must define at least one potential capability")
        if any(item.provider_id != self.provider_id for item in items):
            raise ValueError("all capability provider_id values must match the connector")
        if len({item.capability_id for item in items}) != len(items):
            raise ValueError("connector capability identifiers must be unique")
        self._capabilities = items

    @property
    def capabilities(self) -> tuple[CapabilityMetadata, ...]:
        return self._capabilities

    @abstractmethod
    async def status(self) -> ProviderStatus:
        """Return a live, credential-safe provider status observation."""

    @abstractmethod
    async def execute(
        self,
        capability: CapabilityMetadata,
        request: CapabilityRequest,
    ) -> ConnectorResult:
        """Execute one validated request against the external provider."""

    async def verify(
        self,
        capability: CapabilityMetadata,
        request: CapabilityRequest,
        result: ConnectorResult,
    ) -> VerificationResult:
        """Verify a provider-accepted write; adapters override when supported."""

        return VerificationResult.unverified("Provider does not implement verification")
