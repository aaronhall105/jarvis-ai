"""Connector adapter for the existing verified Home Assistant tool layer."""

from __future__ import annotations

import json
from typing import Any, Mapping

from app.admin_engine import AdminEngine, AdminEngineError
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
from app.home_assistant import HomeAssistantClient, connection_test_with_timeout
from app.tool_engine import ToolEngine


HOME_ASSISTANT_CAPABILITIES = (
    CapabilityMetadata(
        "homeassistant.read",
        "homeassistant",
        "Read Home Assistant state",
        "Fresh entity, area and presence evidence.",
        access=CapabilityAccess.READ,
        timeout_seconds=12,
    ),
    CapabilityMetadata(
        "homeassistant.control",
        "homeassistant",
        "Control Home Assistant devices",
        "Existing grounded light and switch controls.",
        access=CapabilityAccess.WRITE,
        risk=RiskLevel.MEDIUM,
        confirmation=ConfirmationMode.NONE,
        verification=VerificationMode.REQUIRED,
        timeout_seconds=20,
    ),
    CapabilityMetadata(
        "homeassistant.routine",
        "homeassistant",
        "Run Home Assistant routines",
        "Existing safety-checked scripts, automations and media shortcuts.",
        access=CapabilityAccess.WRITE,
        risk=RiskLevel.MEDIUM,
        confirmation=ConfirmationMode.NONE,
        verification=VerificationMode.OPTIONAL,
        timeout_seconds=25,
    ),
    CapabilityMetadata(
        "homeassistant.media",
        "homeassistant",
        "Control Home Assistant media",
        access=CapabilityAccess.WRITE,
        risk=RiskLevel.MEDIUM,
        confirmation=ConfirmationMode.NONE,
        verification=VerificationMode.OPTIONAL,
        timeout_seconds=20,
    ),
    CapabilityMetadata(
        "homeassistant.notify",
        "homeassistant",
        "Submit household notifications",
        "Home Assistant can accept notification and announcement requests; device delivery is not guaranteed.",
        access=CapabilityAccess.WRITE,
        risk=RiskLevel.MEDIUM,
        confirmation=ConfirmationMode.NONE,
        verification=VerificationMode.NONE,
        timeout_seconds=15,
    ),
    CapabilityMetadata(
        "homeassistant.admin.read",
        "homeassistant",
        "Read Home Assistant configuration",
        access=CapabilityAccess.READ,
        risk=RiskLevel.LOW,
        timeout_seconds=15,
    ),
    CapabilityMetadata(
        "homeassistant.admin.propose",
        "homeassistant",
        "Stage a Home Assistant configuration change",
        access=CapabilityAccess.WRITE,
        risk=RiskLevel.HIGH,
        confirmation=ConfirmationMode.NONE,
        verification=VerificationMode.REQUIRED,
        timeout_seconds=15,
    ),
    CapabilityMetadata(
        "homeassistant.admin.apply",
        "homeassistant",
        "Apply a staged Home Assistant configuration change",
        access=CapabilityAccess.WRITE,
        risk=RiskLevel.CRITICAL,
        confirmation=ConfirmationMode.REQUIRED,
        verification=VerificationMode.REQUIRED,
        timeout_seconds=45,
    ),
    CapabilityMetadata(
        "homeassistant.admin.cancel",
        "homeassistant",
        "Cancel a staged Home Assistant configuration change",
        access=CapabilityAccess.WRITE,
        risk=RiskLevel.MEDIUM,
        confirmation=ConfirmationMode.NONE,
        verification=VerificationMode.REQUIRED,
        timeout_seconds=15,
    ),
)


class HomeAssistantConnector(Connector):
    """Expose the established ToolEngine through the common execution boundary."""

    def __init__(
        self,
        *,
        client: HomeAssistantClient,
        tools: ToolEngine,
        admin: AdminEngine,
    ) -> None:
        super().__init__(
            provider_id="homeassistant",
            name="Home Assistant",
            capabilities=HOME_ASSISTANT_CAPABILITIES,
        )
        self.client = client
        self.tools = tools
        self.admin = admin

    async def status(self) -> ProviderStatus:
        connection = await connection_test_with_timeout(self.client)
        executable: list[str] = []
        reason: str | None = None
        if connection.connected:
            executable.extend(
                capability.capability_id
                for capability in self.capabilities
                if not capability.capability_id.startswith("homeassistant.admin")
            )
            try:
                admin = await self.admin.check_access()
            except Exception:
                admin = {"admin_access": False}
            if admin.get("admin_access"):
                executable.extend(
                    capability.capability_id
                    for capability in self.capabilities
                    if capability.capability_id.startswith("homeassistant.admin")
                )
        else:
            reason = str(connection.message or "Home Assistant is unavailable")[:500]
        return ProviderStatus(
            provider_id=self.provider_id,
            name=self.name,
            configured=bool(self.client.base_url and self.client.token),
            authenticated=bool(connection.connected),
            healthy=bool(connection.connected),
            health_reason=reason,
            setup_requirements=(
                ()
                if self.client.base_url and self.client.token
                else ("Configure HOME_ASSISTANT_URL and HOME_ASSISTANT_TOKEN.",)
            ),
            potential_capabilities=tuple(
                capability.capability_id for capability in self.capabilities
            ),
            executable_capabilities=tuple(executable),
        )

    @staticmethod
    def _failure_message(result: Mapping[str, Any]) -> str:
        error = result.get("error")
        if isinstance(error, Mapping):
            error = error.get("message") or error.get("code")
        return str(
            result.get("response_message")
            or result.get("message")
            or error
            or "Home Assistant rejected the operation."
        )

    @classmethod
    def _provider_result(cls, result: Mapping[str, Any]) -> ConnectorResult:
        proposal = result.get("proposal")
        nested_proposal_id = proposal.get("proposal_id") if isinstance(proposal, Mapping) else None
        provider_reference = next(
            (
                str(value).strip()
                for value in (
                    result.get("proposal_id"),
                    nested_proposal_id,
                    result.get("job_id"),
                    result.get("event_id"),
                    result.get("entity_id"),
                )
                if value is not None and str(value).strip()
            ),
            None,
        )
        if result.get("outcome_unknown") is True:
            return ConnectorResult.outcome_unknown(cls._failure_message(result))
        # ToolEngine uses ``success`` as a verified-end-state flag for controls.
        # A completed service call is still provider acceptance when the device
        # does not report the requested state in time.
        provider_accepted = (
            result.get("command_sent") is True or result.get("command_accepted") is True
        )
        if result.get("success") is False and not provider_accepted:
            return ConnectorResult.failed(
                cls._failure_message(result),
                retryable=bool(result.get("retryable")),
            )
        return ConnectorResult.succeeded(
            dict(result),
            provider_reference=provider_reference,
        )

    async def execute(
        self,
        capability: CapabilityMetadata,
        request: CapabilityRequest,
    ) -> ConnectorResult:
        operation = str(request.operation or "").strip()
        payload = dict(request.payload)
        try:
            result = await self._dispatch(capability.capability_id, operation, payload, request)
        except (ValueError, TypeError, KeyError, AdminEngineError, json.JSONDecodeError) as exc:
            return ConnectorResult.failed(str(exc), retryable=False)
        return self._provider_result(result)

    async def _dispatch(
        self,
        capability_id: str,
        operation: str,
        payload: dict[str, Any],
        request: CapabilityRequest,
    ) -> Mapping[str, Any]:
        if capability_id == "homeassistant.read":
            if operation == "search_entity_states":
                return await self.tools.search_entity_states(
                    query=str(payload.get("query") or ""),
                    domain=str(payload["domain"]) if payload.get("domain") else None,
                    area_id=str(payload["area_id"]) if payload.get("area_id") else None,
                    state_filter=(
                        str(payload["state_filter"])
                        if payload.get("state_filter") is not None
                        else None
                    ),
                    limit=max(1, min(int(payload.get("limit", 12)), 100)),
                )
            if operation == "list_area_states":
                return await self.tools.list_area_states(
                    area_id=str(payload.get("area_id") or ""),
                    domain=str(payload["domain"]) if payload.get("domain") else None,
                    state_filter=(
                        str(payload["state_filter"])
                        if payload.get("state_filter") is not None
                        else None
                    ),
                    limit=max(1, min(int(payload.get("limit", 30)), 100)),
                )
            if operation == "get_entity_state":
                return await self.tools.get_entity_state(str(payload.get("entity_id") or ""))
            if operation == "inspect_presence":
                return await self.tools.inspect_presence(str(payload.get("reference") or ""))
        elif capability_id == "homeassistant.control":
            action = str(payload.get("action") or "")
            if action not in {"turn_on", "turn_off", "on", "off"}:
                raise ValueError("Unsupported Home Assistant power action")
            turn_on = action in {"turn_on", "on"}
            if operation == "control_area_lights":
                return await self.tools.control_area_lights(
                    area_id=str(payload.get("area_id") or ""),
                    turn_on=turn_on,
                )
            if operation == "control_device":
                return await self.tools.control_device(
                    entity_id=str(payload.get("entity_id") or ""),
                    turn_on=turn_on,
                )
        elif capability_id == "homeassistant.routine":
            if operation == "run_media_shortcut":
                shortcut = str(payload.get("shortcut") or "")
                if shortcut not in self.tools.MEDIA_SHORTCUTS:
                    raise ValueError("Unsupported media shortcut")
                return await self.tools.run_media_shortcut(shortcut)
            if operation == "run_home_routine":
                entity_id = str(payload.get("entity_id") or "")
                routines = {
                    str(item["entity_id"]): item
                    for item in await self.tools.runnable_routines(limit=200)
                }
                routine = routines.get(entity_id)
                if routine is None:
                    raise ValueError("Unknown or unavailable Home Assistant routine")
                await self.admin.validate_runnable_item(
                    str(routine["domain"]),
                    str(routine["config_key"]),
                )
                return await self.tools.run_home_routine(
                    entity_id,
                    name=str(routine["name"]),
                )
        elif capability_id == "homeassistant.media":
            entity_id = str(payload.get("entity_id") or "")
            if operation == "control_media_player":
                return await self.tools.control_media_player(
                    entity_id,
                    str(payload.get("action") or ""),
                )
            if operation == "set_media_volume":
                return await self.tools.set_media_volume(
                    entity_id,
                    int(payload.get("volume_percent", -1)),
                )
        elif capability_id == "homeassistant.notify":
            message = str(payload.get("message") or "").strip()
            if not message:
                raise ValueError("Notification message cannot be empty")
            if operation == "send_mobile_notification":
                return await self.tools.send_mobile_notification(
                    recipient=str(payload.get("recipient") or ""),
                    message=message,
                    title=str(payload.get("title") or "Jarvis"),
                )
            if operation == "announce_message":
                return await self.tools.announce_message(
                    str(payload.get("target") or ""),
                    message,
                )
        elif capability_id == "homeassistant.admin.read":
            domain = str(payload.get("domain") or "").lower()
            if operation == "list_admin_items":
                return await self.admin.list_items(
                    domain,
                    str(payload.get("query") or ""),
                    max(1, min(int(payload.get("limit", 20)), 100)),
                )
            if operation == "get_admin_item_config":
                config_key = str(payload.get("config_key") or "").lower()
                config = await self.admin.get_config(domain, config_key)
                if config is None:
                    raise ValueError("Home Assistant configuration item was not found")
                return {
                    "success": True,
                    "domain": domain,
                    "config_key": config_key,
                    "config": config,
                }
        elif capability_id == "homeassistant.admin.propose":
            if operation != "propose_admin_change":
                raise ValueError("Unsupported admin proposal operation")
            config = payload.get("config")
            if config is None:
                config = json.loads(str(payload.get("config_json") or "{}"))
            if not isinstance(config, dict):
                raise ValueError("The proposed Home Assistant configuration must be an object")
            if not request.conversation_id:
                raise ValueError("A conversation is required for an admin proposal")
            return await self.admin.propose_change(
                conversation_id=request.conversation_id,
                domain=str(payload.get("domain") or "").lower(),
                operation=str(payload.get("operation") or "").lower(),
                config_key=str(payload.get("config_key") or "").lower(),
                name=str(payload.get("name") or ""),
                summary=str(payload.get("summary") or ""),
                config=config,
            )
        elif capability_id == "homeassistant.admin.apply":
            if not request.conversation_id:
                raise ValueError("A conversation is required to apply an admin proposal")
            return await self.admin.apply_pending(request.conversation_id)
        elif capability_id == "homeassistant.admin.cancel":
            if not request.conversation_id:
                raise ValueError("A conversation is required to cancel an admin proposal")
            return await self.admin.cancel_pending(request.conversation_id)
        raise ValueError(f"Unsupported Home Assistant connector operation: {operation}")

    async def verify(
        self,
        capability: CapabilityMetadata,
        request: CapabilityRequest,
        result: ConnectorResult,
    ) -> VerificationResult:
        data = dict(result.data)
        if data.get("verified") is True:
            return VerificationResult.verified(
                {
                    key: data[key]
                    for key in (
                        "entity_id",
                        "area_id",
                        "state",
                        "current_state",
                        "proposal_id",
                        "config_key",
                    )
                    if key in data
                }
            )
        if capability.capability_id == "homeassistant.admin.propose" and data.get("proposal_id"):
            return VerificationResult.verified(
                {"proposal_id": data["proposal_id"], "persisted": True}
            )
        if capability.capability_id == "homeassistant.admin.propose":
            proposal = data.get("proposal")
            if isinstance(proposal, Mapping) and proposal.get("proposal_id"):
                return VerificationResult.verified(
                    {
                        "proposal_id": proposal["proposal_id"],
                        "persisted": True,
                    }
                )
        if (
            capability.capability_id == "homeassistant.admin.apply"
            and data.get("success") is True
            and data.get("proposal_id")
        ):
            evidence: dict[str, Any] = {
                key: data[key]
                for key in ("proposal_id", "domain", "config_key", "runtime_loaded")
                if key in data
            }
            # AdminEngine returns success only after re-reading and validating
            # the stored Home Assistant configuration.
            evidence["persisted"] = True
            return VerificationResult.verified(evidence)
        if (
            capability.capability_id == "homeassistant.admin.cancel"
            and data.get("success") is True
            and data.get("proposal_id")
        ):
            return VerificationResult.verified(
                {"proposal_id": data["proposal_id"], "cancelled": True}
            )
        return VerificationResult.unverified(
            "Home Assistant accepted the operation but did not provide post-state verification"
        )
