"""Strict provider-neutral contracts for Jarvis external services.

This module contains service-layer contracts and unavailable setup descriptors
only.  It does not contain mock providers or optimistic fallbacks.  Executable
capability metadata and provider status deliberately reuse ``app.connectors``;
the core registry remains the only execution boundary.  Domain-specific methods
below describe private adapter implementation surfaces; planners, models and API
routes must invoke them only through ``Connector.execute`` via the registry so
policy, health, receipts and verification cannot be bypassed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from app.connectors import (
    ActionReceipt,
    CapabilityAccess,
    CapabilityMetadata,
    CapabilityRequest,
    ConnectorResult,
    ProviderStatus,
    ReceiptStatus,
    VerificationResult,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConnectorError(RuntimeError):
    """Base error raised by external connector contracts."""


class ConnectorUnavailableError(ConnectorError):
    def __init__(self, provider_id: str, reason: str) -> None:
        self.provider_id = provider_id
        self.reason = reason
        super().__init__(f"Connector '{provider_id}' is unavailable: {reason}")


@dataclass(frozen=True, slots=True)
class PotentialCapability:
    """Setup-page descriptor; never an executable registry definition."""

    capability_id: str
    access: CapabilityAccess
    repeatable: bool = False
    requires_confirmation: bool = False
    verification_supported: bool = False
    scopes: tuple[str, ...] = ()
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["access"] = self.access.value
        return data


@dataclass(frozen=True, slots=True)
class SetupRequirements:
    summary: str
    oauth_required: bool = False
    interactive_login_required: bool = False
    environment_variables: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    capabilities_after_setup: tuple[PotentialCapability, ...] = ()

    def to_redacted_dict(self) -> dict[str, Any]:
        """Describe required secret names and scopes, never secret values."""

        return {
            "summary": self.summary,
            "oauth_required": self.oauth_required,
            "interactive_login_required": self.interactive_login_required,
            "environment_variables": list(self.environment_variables),
            "scopes": list(self.scopes),
            "capabilities_after_setup": [
                capability.to_dict() for capability in self.capabilities_after_setup
            ],
        }


@dataclass(frozen=True, slots=True)
class ExecutionError:
    code: str
    message: str
    retryable: bool = False
    provider_code: str | None = None


ExternalActionReceipt = ActionReceipt


@runtime_checkable
class ServiceConnector(Protocol):
    """Typed view of a connector that is executed only through ConnectorRegistry."""

    provider_id: str

    @property
    def capabilities(self) -> tuple[CapabilityMetadata, ...]: ...

    async def status(self) -> ProviderStatus: ...

    async def execute(
        self, capability: CapabilityMetadata, request: CapabilityRequest
    ) -> ConnectorResult: ...

    async def verify(
        self,
        capability: CapabilityMetadata,
        request: CapabilityRequest,
        result: ConnectorResult,
    ) -> VerificationResult: ...


@dataclass(frozen=True, slots=True)
class BrowserElement:
    element_id: str
    role: str
    name: str
    value: str | None = None
    enabled: bool = True
    visible: bool = True


@dataclass(frozen=True, slots=True)
class BrowserPageState:
    url: str
    title: str
    elements: tuple[BrowserElement, ...] = ()
    text: str = ""
    authenticated: bool | None = None
    auth_required: bool = False
    redirect_chain: tuple[str, ...] = ()
    captured_at: str = field(default_factory=_now_iso)


@dataclass(frozen=True, slots=True)
class BrowserActionResult:
    success: bool
    operation: str
    state: BrowserPageState | None
    state_verified: bool
    download_reference: str | None = None
    error: ExecutionError | None = None


@runtime_checkable
class BrowserConnector(ServiceConnector, Protocol):
    async def open(self, url: str) -> BrowserActionResult: ...

    async def inspect(self) -> BrowserPageState: ...

    async def click(self, element_id: str) -> BrowserActionResult: ...

    async def type(self, element_id: str, text: str) -> BrowserActionResult: ...

    async def select(self, element_id: str, value: str) -> BrowserActionResult: ...

    async def scroll(self, direction: str, amount: int | None = None) -> BrowserActionResult: ...

    async def wait(self, condition: str, timeout_seconds: float) -> BrowserActionResult: ...

    async def back(self) -> BrowserActionResult: ...

    async def upload(self, element_id: str, file_reference: str) -> BrowserActionResult: ...

    async def download(self, element_id: str) -> BrowserActionResult: ...

    async def extract(self, selector: str | None = None) -> Mapping[str, Any]: ...

    async def current_state(self) -> BrowserPageState: ...


@dataclass(frozen=True, slots=True)
class EmailAddress:
    address: str
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class EmailMessage:
    message_id: str
    thread_id: str
    sender: EmailAddress
    recipients: tuple[EmailAddress, ...]
    subject: str
    body_text: str
    sent_at: str
    unread: bool = False
    important: bool = False
    attachment_metadata: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class EmailThread:
    thread_id: str
    messages: tuple[EmailMessage, ...]


@dataclass(frozen=True, slots=True)
class EmailDraft:
    draft_id: str
    recipients: tuple[EmailAddress, ...]
    subject: str
    body_text: str
    thread_id: str | None = None
    created_at: str = field(default_factory=_now_iso)


@dataclass(frozen=True, slots=True)
class EmailSendReceipt:
    """API acceptance is execution evidence, not a delivery guarantee."""

    provider_id: str
    message_id: str | None
    thread_id: str | None
    accepted: bool
    delivery_confirmed: bool
    provider_status: str
    action_receipt: ExternalActionReceipt


@runtime_checkable
class EmailConnector(ServiceConnector, Protocol):
    async def search(
        self, query: str, *, unread: bool | None = None, important: bool | None = None
    ) -> Sequence[EmailThread]: ...

    async def read_thread(self, thread_id: str) -> EmailThread: ...

    async def read_message(self, message_id: str) -> EmailMessage: ...

    async def create_draft(
        self,
        recipients: Sequence[EmailAddress],
        subject: str,
        body_text: str,
        *,
        thread_id: str | None = None,
    ) -> EmailDraft: ...

    async def send_draft(
        self, draft_id: str, *, conversation_id: str, approval_reference: str
    ) -> EmailSendReceipt: ...

    async def reply(
        self,
        thread_id: str,
        body_text: str,
        *,
        conversation_id: str,
        approval_reference: str,
    ) -> EmailSendReceipt: ...

    async def archive(self, thread_id: str) -> ExternalActionReceipt: ...

    async def label(self, thread_id: str, labels: Sequence[str]) -> ExternalActionReceipt: ...


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    event_id: str
    calendar_id: str
    title: str
    starts_at: str
    ends_at: str
    timezone: str
    attendees: tuple[str, ...] = ()
    location: str | None = None
    status: str = "confirmed"


@dataclass(frozen=True, slots=True)
class AvailabilityWindow:
    starts_at: str
    ends_at: str
    available: bool
    conflicting_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CalendarWriteResult:
    event: CalendarEvent | None
    receipt: ExternalActionReceipt | None
    conflict: AvailabilityWindow | None = None
    error: ExecutionError | None = None


@runtime_checkable
class CalendarConnector(ServiceConnector, Protocol):
    async def timezone(self) -> str: ...

    async def list_events(self, starts_at: str, ends_at: str) -> Sequence[CalendarEvent]: ...

    async def search_events(
        self,
        query: str,
        *,
        starts_at: str | None = None,
        ends_at: str | None = None,
        timezone: str | None = None,
    ) -> Sequence[CalendarEvent]: ...

    async def availability(self, starts_at: str, ends_at: str) -> AvailabilityWindow: ...

    async def create_event(
        self, event: CalendarEvent, *, conversation_id: str, approval_reference: str | None = None
    ) -> CalendarWriteResult: ...

    async def update_event(
        self,
        event_id: str,
        changes: Mapping[str, Any],
        *,
        conversation_id: str,
        approval_reference: str | None = None,
    ) -> CalendarWriteResult: ...

    async def cancel_event(
        self, event_id: str, *, conversation_id: str, approval_reference: str | None = None
    ) -> ExternalActionReceipt: ...


@dataclass(frozen=True, slots=True)
class Contact:
    contact_id: str
    display_name: str
    email_addresses: tuple[str, ...] = ()
    phone_numbers: tuple[str, ...] = ()
    organisation: str | None = None


@dataclass(frozen=True, slots=True)
class ContactResolution:
    query: str
    matches: tuple[Contact, ...]
    exact: bool
    ambiguous: bool

    @property
    def resolved(self) -> Contact | None:
        return self.matches[0] if self.exact and len(self.matches) == 1 else None


@runtime_checkable
class ContactsConnector(ServiceConnector, Protocol):
    async def search(self, query: str) -> ContactResolution: ...

    async def resolve_exact(self, query: str) -> ContactResolution: ...


@dataclass(frozen=True, slots=True)
class Participant:
    provider_identity: str
    display_name: str | None = None
    contact_id: str | None = None


@dataclass(frozen=True, slots=True)
class CommunicationMessage:
    message_id: str
    thread_id: str
    sender: Participant
    recipients: tuple[Participant, ...]
    body: str
    sent_at: str
    provider_id: str


@dataclass(frozen=True, slots=True)
class CommunicationThread:
    thread_id: str
    provider_id: str
    participants: tuple[Participant, ...]
    messages: tuple[CommunicationMessage, ...]


@runtime_checkable
class CommunicationConnector(ServiceConnector, Protocol):
    async def list_threads(self, query: str | None = None) -> Sequence[CommunicationThread]: ...

    async def send(
        self,
        recipients: Sequence[Participant],
        body: str,
        *,
        conversation_id: str,
        approval_reference: str,
        thread_id: str | None = None,
    ) -> ExternalActionReceipt: ...


@dataclass(frozen=True, slots=True)
class SocialPost:
    post_id: str
    text: str
    media_references: tuple[str, ...]
    published_at: str | None
    metrics: Mapping[str, int] | None = None


@dataclass(frozen=True, slots=True)
class SocialDraft:
    draft_id: str
    text: str
    media_references: tuple[str, ...] = ()
    created_at: str = field(default_factory=_now_iso)


@dataclass(frozen=True, slots=True)
class SocialPublishReceipt:
    post_id: str | None
    published: bool
    provider_status: str
    action_receipt: ExternalActionReceipt


@runtime_checkable
class SocialConnector(ServiceConnector, Protocol):
    async def read_profile(self) -> Mapping[str, Any]: ...

    async def read_posts(self, limit: int = 20) -> Sequence[SocialPost]: ...

    async def create_draft(self, text: str, media_references: Sequence[str]) -> SocialDraft: ...

    async def publish(
        self, draft_id: str, *, conversation_id: str, approval_reference: str
    ) -> SocialPublishReceipt: ...


@dataclass(frozen=True, slots=True)
class TravelOption:
    option_id: str
    provider_id: str
    kind: str
    title: str
    price: Mapping[str, Any] | None
    availability_checked_at: str
    provider_reference: str
    details: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class TravelConnector(ServiceConnector, Protocol):
    async def search_transport(self, criteria: Mapping[str, Any]) -> Sequence[TravelOption]: ...

    async def search_accommodation(self, criteria: Mapping[str, Any]) -> Sequence[TravelOption]: ...

    async def reserve(
        self,
        option_id: str,
        *,
        conversation_id: str,
        approval_reference: str,
    ) -> ExternalActionReceipt: ...


@dataclass(frozen=True, slots=True)
class ProductOffer:
    offer_id: str
    provider_id: str
    product_name: str
    canonical_url: str
    price: Mapping[str, Any] | None
    in_stock: bool | None
    retrieved_at: str
    specifications: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class ShoppingConnector(ServiceConnector, Protocol):
    async def search_products(self, query: str) -> Sequence[ProductOffer]: ...

    async def get_offer(self, offer_id: str) -> ProductOffer: ...

    async def purchase(
        self,
        offer_id: str,
        *,
        conversation_id: str,
        approval_reference: str,
    ) -> ExternalActionReceipt: ...


@runtime_checkable
class DatingConnector(ServiceConnector, Protocol):
    async def read_profile(self) -> Mapping[str, Any]: ...

    async def update_profile(
        self,
        changes: Mapping[str, Any],
        *,
        conversation_id: str,
        approval_reference: str,
    ) -> ExternalActionReceipt: ...

    async def send_selected_message(
        self,
        thread_id: str,
        message: str,
        *,
        conversation_id: str,
        approval_reference: str,
    ) -> ExternalActionReceipt: ...


@dataclass(frozen=True, slots=True)
class SetupCatalogEntry:
    provider_id: str
    name: str
    reason: str
    setup: SetupRequirements


def _capabilities(
    read: Sequence[str],
    write: Sequence[str] = (),
    *,
    repeatable: Sequence[str] = (),
    verified_writes: Sequence[str] = (),
    no_confirmation_writes: Sequence[str] = (),
) -> tuple[PotentialCapability, ...]:
    items = [
        PotentialCapability(
            item,
            CapabilityAccess.READ,
            repeatable=item in repeatable,
            verification_supported=True,
        )
        for item in read
    ]
    items.extend(
        PotentialCapability(
            item,
            CapabilityAccess.WRITE,
            repeatable=item in repeatable,
            requires_confirmation=item not in no_confirmation_writes,
            verification_supported=item in verified_writes,
        )
        for item in write
    )
    return tuple(items)


def _social_setup_entry(provider_id: str, name: str) -> SetupCatalogEntry:
    def capability(operation: str) -> str:
        return f"{provider_id}.{operation}"

    return SetupCatalogEntry(
        provider_id,
        name,
        f"No supported {name} adapter and authorised account are configured",
        SetupRequirements(
            f"Install and configure a supported {name} adapter, then authorise "
            "an account through its official API or a semantic browser provider.",
            oauth_required=True,
            capabilities_after_setup=_capabilities(
                [
                    capability("read_profile"),
                    capability("read_posts"),
                    capability("read_metrics"),
                ],
                [
                    capability("draft"),
                    capability("publish"),
                    capability("reply"),
                    capability("update_profile"),
                ],
                repeatable=[
                    capability("read_profile"),
                    capability("read_posts"),
                    capability("read_metrics"),
                ],
                verified_writes=[
                    capability("draft"),
                    capability("publish"),
                    capability("reply"),
                    capability("update_profile"),
                ],
                no_confirmation_writes=[capability("draft")],
            ),
        ),
    )


UNAVAILABLE_CONNECTOR_CATALOG: Mapping[str, SetupCatalogEntry] = {
    "browser": SetupCatalogEntry(
        "browser",
        "Browser automation",
        "No semantic browser automation provider is configured",
        SetupRequirements(
            "Configure a supported semantic browser backend and an authorised browser profile.",
            interactive_login_required=True,
            capabilities_after_setup=_capabilities(
                [
                    "browser.inspect",
                    "browser.extract",
                    "browser.current_state",
                ],
                [
                    "browser.open",
                    "browser.click",
                    "browser.type",
                    "browser.select",
                    "browser.scroll",
                    "browser.wait",
                    "browser.back",
                    "browser.upload",
                    "browser.download",
                ],
                repeatable=["browser.inspect", "browser.extract", "browser.current_state"],
                verified_writes=[
                    "browser.open",
                    "browser.click",
                    "browser.type",
                    "browser.select",
                    "browser.scroll",
                    "browser.wait",
                    "browser.back",
                    "browser.upload",
                    "browser.download",
                ],
            ),
        ),
    ),
    "gmail": SetupCatalogEntry(
        "gmail",
        "Gmail",
        "No supported Gmail adapter and authorised account are configured",
        SetupRequirements(
            "Install and configure a supported Gmail API adapter, then complete "
            "OAuth with the minimum required scopes.",
            oauth_required=True,
            capabilities_after_setup=_capabilities(
                ["gmail.search", "gmail.read", "gmail.thread"],
                [
                    "gmail.draft",
                    "gmail.reply",
                    "gmail.send",
                    "gmail.archive",
                    "gmail.label",
                ],
                repeatable=["gmail.search", "gmail.read", "gmail.thread"],
                verified_writes=[
                    "gmail.draft",
                    "gmail.reply",
                    "gmail.send",
                    "gmail.archive",
                    "gmail.label",
                ],
                no_confirmation_writes=["gmail.draft"],
            ),
        ),
    ),
    "calendar": SetupCatalogEntry(
        "calendar",
        "Calendar",
        "No supported calendar adapter and authorised account are configured",
        SetupRequirements(
            "Install and configure a supported calendar adapter, then complete "
            "OAuth and choose the default timezone and calendar.",
            oauth_required=True,
            capabilities_after_setup=_capabilities(
                [
                    "calendar.list",
                    "calendar.search",
                    "calendar.availability",
                    "calendar.timezone",
                ],
                ["calendar.create", "calendar.update", "calendar.cancel"],
                repeatable=[
                    "calendar.list",
                    "calendar.search",
                    "calendar.availability",
                    "calendar.timezone",
                ],
                verified_writes=["calendar.create", "calendar.update", "calendar.cancel"],
            ),
        ),
    ),
    "contacts": SetupCatalogEntry(
        "contacts",
        "Contacts",
        "No supported contacts adapter and authorised account are configured",
        SetupRequirements(
            "Install and configure a supported contacts adapter, then authorise "
            "read-only account access.",
            oauth_required=True,
            capabilities_after_setup=_capabilities(
                ["contacts.search", "contacts.resolve"],
                repeatable=["contacts.search", "contacts.resolve"],
            ),
        ),
    ),
    "communication": SetupCatalogEntry(
        "communication",
        "Communications",
        "No supported communications adapter and authorised account are configured",
        SetupRequirements(
            "Install and configure a supported messaging or notification adapter, "
            "then authorise its account.",
            oauth_required=True,
            capabilities_after_setup=_capabilities(
                ["communication.read"],
                ["communication.send"],
                repeatable=["communication.read"],
                verified_writes=["communication.send"],
            ),
        ),
    ),
    **{
        provider_id: _social_setup_entry(provider_id, name)
        for provider_id, name in (
            ("instagram", "Instagram"),
            ("facebook", "Facebook"),
            ("tiktok", "TikTok"),
            ("x_social", "X"),
        )
    },
    "travel": SetupCatalogEntry(
        "travel",
        "Travel",
        "No live travel inventory or booking provider is configured",
        SetupRequirements(
            "Configure a supported travel search provider; booking requires a verified write API.",
            capabilities_after_setup=_capabilities(
                ["travel.search_transport", "travel.search_accommodation"],
                ["travel.reserve"],
                repeatable=["travel.search_transport", "travel.search_accommodation"],
                verified_writes=["travel.reserve"],
            ),
        ),
    ),
    "shopping": SetupCatalogEntry(
        "shopping",
        "Shopping",
        "No live product or purchasing provider is configured",
        SetupRequirements(
            "Configure live product data; purchasing requires a verified write provider.",
            capabilities_after_setup=_capabilities(
                ["shopping.search", "shopping.offer"],
                ["shopping.purchase"],
                repeatable=["shopping.search", "shopping.offer"],
                verified_writes=["shopping.purchase"],
            ),
        ),
    ),
    "dating": SetupCatalogEntry(
        "dating",
        "Dating profile",
        "No supported user-authorised dating account connector is configured",
        SetupRequirements(
            "Configure an authorised provider; security challenges always require user intervention.",
            oauth_required=True,
            interactive_login_required=True,
            capabilities_after_setup=_capabilities(
                ["dating.read_profile"],
                ["dating.update_profile", "dating.send_selected_message"],
                verified_writes=["dating.update_profile", "dating.send_selected_message"],
            ),
        ),
    ),
}


class UnavailableConnector:
    """Truthful setup object; deliberately not a registerable core Connector."""

    def __init__(self, entry: SetupCatalogEntry) -> None:
        self.entry = entry

    @property
    def provider_id(self) -> str:
        return self.entry.provider_id

    @property
    def setup(self) -> SetupRequirements:
        return self.entry.setup

    @classmethod
    def for_service(cls, provider_id: str) -> "UnavailableConnector":
        try:
            entry = UNAVAILABLE_CONNECTOR_CATALOG[provider_id]
        except KeyError as exc:
            raise ValueError(f"Unknown connector setup catalog entry: {provider_id}") from exc
        return cls(entry)

    async def status(self) -> ProviderStatus:
        # Potential capabilities are setup hints only.  ProviderStatus reports an
        # explicit empty executable set, and this object has no core ``capabilities``
        # property, so it cannot be registered as an executable connector.
        requirements = [self.entry.setup.summary]
        requirements.extend(
            f"Configure secret: {name}" for name in self.entry.setup.environment_variables
        )
        if self.entry.setup.oauth_required:
            requirements.append("Complete provider OAuth setup")
        if self.entry.setup.interactive_login_required:
            requirements.append("Complete interactive login when prompted")
        return ProviderStatus(
            provider_id=self.entry.provider_id,
            name=self.entry.name,
            configured=False,
            authenticated=False,
            healthy=False,
            health_reason=self.entry.reason,
            setup_requirements=tuple(requirements),
            scopes=frozenset(self.entry.setup.scopes),
            potential_capabilities=tuple(
                item.capability_id for item in self.entry.setup.capabilities_after_setup
            ),
            executable_capabilities=(),
        )

    async def execute(self, capability_id: str, arguments: Mapping[str, Any]) -> None:
        del capability_id, arguments
        raise ConnectorUnavailableError(self.entry.provider_id, self.entry.reason)


def unavailable_connector_catalog() -> tuple[UnavailableConnector, ...]:
    return tuple(UnavailableConnector(entry) for entry in UNAVAILABLE_CONNECTOR_CATALOG.values())


__all__ = [
    "ActionReceipt",
    "AvailabilityWindow",
    "BrowserActionResult",
    "BrowserConnector",
    "BrowserElement",
    "BrowserPageState",
    "CalendarConnector",
    "CalendarEvent",
    "CalendarWriteResult",
    "CapabilityAccess",
    "CapabilityMetadata",
    "CommunicationConnector",
    "CommunicationMessage",
    "CommunicationThread",
    "ConnectorError",
    "ConnectorUnavailableError",
    "Contact",
    "ContactResolution",
    "ContactsConnector",
    "DatingConnector",
    "EmailAddress",
    "EmailConnector",
    "EmailDraft",
    "EmailMessage",
    "EmailSendReceipt",
    "EmailThread",
    "ExecutionError",
    "ExternalActionReceipt",
    "Participant",
    "ProductOffer",
    "PotentialCapability",
    "ProviderStatus",
    "ReceiptStatus",
    "ServiceConnector",
    "SetupCatalogEntry",
    "SetupRequirements",
    "ShoppingConnector",
    "SocialConnector",
    "SocialDraft",
    "SocialPost",
    "SocialPublishReceipt",
    "TravelConnector",
    "TravelOption",
    "UNAVAILABLE_CONNECTOR_CATALOG",
    "UnavailableConnector",
    "unavailable_connector_catalog",
]
