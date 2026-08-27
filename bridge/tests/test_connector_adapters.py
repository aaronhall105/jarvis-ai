from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.connectors import (
    CapabilityRequest,
    ProviderResultStatus,
    VerificationStatus,
)
from app.home_assistant_connector import HomeAssistantConnector
from app.web_connector import OpenAIWebSearchConnector, PublicWebFetchConnector


def _capability(connector: object, capability_id: str):
    return next(
        item
        for item in connector.capabilities  # type: ignore[attr-defined]
        if item.capability_id == capability_id
    )


def _home_connector(*, tools: object, admin: object) -> HomeAssistantConnector:
    client = SimpleNamespace(
        base_url="https://home-assistant.example",
        token="configured-token",
    )
    return HomeAssistantConnector(client=client, tools=tools, admin=admin)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_home_control_preserves_provider_acceptance_when_state_is_unverified() -> None:
    tools = SimpleNamespace(
        control_device=AsyncMock(
            return_value={
                "success": False,
                "command_sent": True,
                "verified": False,
                "entity_id": "light.office",
                "response_message": "The command was sent but state did not change.",
            }
        )
    )
    connector = _home_connector(tools=tools, admin=SimpleNamespace())
    capability = _capability(connector, "homeassistant.control")
    request = CapabilityRequest(
        capability_id=capability.capability_id,
        operation="control_device",
        payload={"entity_id": "light.office", "action": "turn_on"},
    )

    result = await connector.execute(capability, request)
    verification = await connector.verify(capability, request, result)

    assert result.status is ProviderResultStatus.SUCCEEDED
    assert result.provider_reference == "light.office"
    assert verification.status is VerificationStatus.UNVERIFIED


@pytest.mark.asyncio
async def test_home_control_keeps_pre_call_rejections_as_failures() -> None:
    tools = SimpleNamespace(
        control_device=AsyncMock(
            return_value={
                "success": False,
                "verified": False,
                "entity_id": "light.missing",
                "response_message": "The device is unavailable.",
            }
        )
    )
    connector = _home_connector(tools=tools, admin=SimpleNamespace())
    capability = _capability(connector, "homeassistant.control")

    result = await connector.execute(
        capability,
        CapabilityRequest(
            capability_id=capability.capability_id,
            operation="control_device",
            payload={"entity_id": "light.missing", "action": "turn_off"},
        ),
    )

    assert result.status is ProviderResultStatus.FAILED


@pytest.mark.asyncio
async def test_admin_proposal_uses_nested_reference_and_persistence_evidence() -> None:
    admin = SimpleNamespace(
        propose_change=AsyncMock(
            return_value={
                "success": True,
                "proposal": {
                    "proposal_id": "proposal-123",
                    "domain": "automation",
                    "config_key": "jarvis_test",
                },
                "requires_confirmation": True,
            }
        )
    )
    connector = _home_connector(tools=SimpleNamespace(), admin=admin)
    capability = _capability(connector, "homeassistant.admin.propose")
    request = CapabilityRequest(
        capability_id=capability.capability_id,
        conversation_id="conversation-1",
        operation="propose_admin_change",
        payload={
            "domain": "automation",
            "operation": "create",
            "config_key": "jarvis_test",
            "name": "Test automation",
            "summary": "Create a test automation.",
            "config": {"alias": "Test automation"},
        },
    )

    result = await connector.execute(capability, request)
    verification = await connector.verify(capability, request, result)

    assert result.status is ProviderResultStatus.SUCCEEDED
    assert result.provider_reference == "proposal-123"
    assert verification.status is VerificationStatus.VERIFIED
    assert verification.evidence == {
        "proposal_id": "proposal-123",
        "persisted": True,
    }


@pytest.mark.asyncio
async def test_admin_apply_exposes_existing_storage_verification() -> None:
    admin = SimpleNamespace(
        apply_pending=AsyncMock(
            return_value={
                "success": True,
                "proposal_id": "proposal-456",
                "domain": "script",
                "config_key": "jarvis_test",
                "runtime_loaded": False,
            }
        )
    )
    connector = _home_connector(tools=SimpleNamespace(), admin=admin)
    capability = _capability(connector, "homeassistant.admin.apply")
    request = CapabilityRequest(
        capability_id=capability.capability_id,
        conversation_id="conversation-1",
        operation="apply_admin_change",
        confirmed=True,
    )

    result = await connector.execute(capability, request)
    verification = await connector.verify(capability, request, result)

    assert result.status is ProviderResultStatus.SUCCEEDED
    assert result.provider_reference == "proposal-456"
    assert verification.status is VerificationStatus.VERIFIED
    assert verification.evidence == {
        "proposal_id": "proposal-456",
        "domain": "script",
        "config_key": "jarvis_test",
        "persisted": True,
        "runtime_loaded": False,
    }


@pytest.mark.asyncio
async def test_admin_cancel_retains_the_cancelled_proposal_reference() -> None:
    admin = SimpleNamespace(
        cancel_pending=AsyncMock(
            return_value={
                "success": True,
                "proposal_id": "proposal-789",
            }
        )
    )
    connector = _home_connector(tools=SimpleNamespace(), admin=admin)
    capability = _capability(connector, "homeassistant.admin.cancel")
    request = CapabilityRequest(
        capability_id=capability.capability_id,
        conversation_id="conversation-1",
        operation="cancel_admin_change",
    )

    result = await connector.execute(capability, request)
    verification = await connector.verify(capability, request, result)

    assert result.provider_reference == "proposal-789"
    assert verification.status is VerificationStatus.VERIFIED
    assert verification.evidence == {
        "proposal_id": "proposal-789",
        "cancelled": True,
    }


@pytest.mark.asyncio
async def test_web_connectors_have_independent_provider_health() -> None:
    search = SimpleNamespace(
        health=AsyncMock(
            return_value={
                "configured": False,
                "authenticated": False,
                "healthy": False,
                "reason": "Search is disabled.",
            }
        )
    )
    fetcher = SimpleNamespace(
        fetch=AsyncMock(),
        health=AsyncMock(return_value={"healthy": True, "reason": None}),
        aclose=AsyncMock(),
    )
    search_connector = OpenAIWebSearchConnector(search=search)  # type: ignore[arg-type]
    fetch_connector = PublicWebFetchConnector(fetcher=fetcher)  # type: ignore[arg-type]

    search_status = await search_connector.status()
    fetch_status = await fetch_connector.status()

    assert search_connector.provider_id == "openai_web_search"
    assert tuple(item.capability_id for item in search_connector.capabilities) == ("web.search",)
    assert search_status.available is False
    assert search_status.executable_capabilities == ()
    assert fetch_connector.provider_id == "public_web_fetch"
    assert tuple(item.capability_id for item in fetch_connector.capabilities) == ("web.fetch",)
    assert fetch_status.available is True
    assert fetch_status.executable_capabilities == ("web.fetch",)


@pytest.mark.asyncio
async def test_public_fetch_probe_failure_removes_executable_capability() -> None:
    fetcher = SimpleNamespace(
        fetch=AsyncMock(),
        health=AsyncMock(return_value={"healthy": False, "reason": "Network probe failed."}),
        aclose=AsyncMock(),
    )
    connector = PublicWebFetchConnector(fetcher=fetcher)  # type: ignore[arg-type]

    status = await connector.status()

    assert status.configured is True
    assert status.healthy is False
    assert status.executable_capabilities == ()
    assert status.health_reason == "Network probe failed."


@pytest.mark.asyncio
async def test_web_rejects_invalid_inputs_without_calling_clients() -> None:
    search = SimpleNamespace(search=AsyncMock())
    fetcher = SimpleNamespace(fetch=AsyncMock(), aclose=AsyncMock())
    search_connector = OpenAIWebSearchConnector(search=search)  # type: ignore[arg-type]
    fetch_connector = PublicWebFetchConnector(fetcher=fetcher)  # type: ignore[arg-type]

    search_capability = _capability(search_connector, "web.search")
    search_result = await search_connector.execute(
        search_capability,
        CapabilityRequest(
            capability_id=search_capability.capability_id,
            payload={"query": "current evidence", "limit": "not-an-integer"},
        ),
    )
    fetch_capability = _capability(fetch_connector, "web.fetch")
    fetch_result = await fetch_connector.execute(
        fetch_capability,
        CapabilityRequest(
            capability_id=fetch_capability.capability_id,
            payload={"url": ""},
        ),
    )

    assert search_result.status is ProviderResultStatus.FAILED
    assert search_result.retryable is False
    assert fetch_result.status is ProviderResultStatus.FAILED
    assert fetch_result.retryable is False
    search.search.assert_not_awaited()
    fetcher.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_web_search_bounds_limit_and_preserves_provider_reference() -> None:
    evidence = SimpleNamespace(
        provider_reference="response-123",
        as_dict=lambda: {
            "query": "current evidence",
            "sources": [{"url": "https://example.com/source"}],
        },
    )
    search = SimpleNamespace(search=AsyncMock(return_value=evidence))
    connector = OpenAIWebSearchConnector(search=search)  # type: ignore[arg-type]
    capability = _capability(connector, "web.search")

    result = await connector.execute(
        capability,
        CapabilityRequest(
            capability_id=capability.capability_id,
            payload={"query": " current evidence ", "limit": 999},
        ),
    )

    assert result.status is ProviderResultStatus.SUCCEEDED
    assert result.provider_reference == "response-123"
    search.search.assert_awaited_once_with("current evidence", limit=20)


@pytest.mark.asyncio
async def test_web_fetch_uses_canonical_url_as_provider_reference() -> None:
    page = SimpleNamespace(
        canonical_url="https://example.com/canonical",
        as_dict=lambda: {
            "url": "https://example.com/story",
            "canonical_url": "https://example.com/canonical",
            "text": "Evidence",
        },
    )
    fetcher = SimpleNamespace(fetch=AsyncMock(return_value=page), aclose=AsyncMock())
    connector = PublicWebFetchConnector(fetcher=fetcher)  # type: ignore[arg-type]
    capability = _capability(connector, "web.fetch")

    result = await connector.execute(
        capability,
        CapabilityRequest(
            capability_id=capability.capability_id,
            payload={"url": "https://example.com/story"},
        ),
    )

    assert result.status is ProviderResultStatus.SUCCEEDED
    assert result.provider_reference == "https://example.com/canonical"
