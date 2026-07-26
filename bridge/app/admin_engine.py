import asyncio
import json
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.home_assistant import (
    HomeAssistantClient,
    HomeAssistantError,
    HomeAssistantHTTPError,
)


class AdminEngineError(RuntimeError):
    """Raised when a persistent Home Assistant change is unsafe or fails."""


@dataclass
class PendingAdminChange:
    proposal_id: str
    conversation_id: str
    domain: str
    operation: str
    config_key: str
    name: str
    summary: str
    config_json: str
    previous_config_json: str | None
    status: str
    created_at: str
    expires_at: str
    applied_at: str | None
    error: str | None


class AdminEngine:
    """Safe, confirmation-gated Home Assistant automation/script editor."""

    ALLOWED_DOMAINS = {"automation", "script"}
    ALLOWED_OPERATIONS = {"create", "update"}
    SAFE_KEY_PATTERN = re.compile(r"^[a-z0-9_]{3,64}$")

    NOTIFY_ACTION_ALIASES = {
        "notify.mobile_app_sm_g996b": "notify.mobile_app_aaron_s_phone",
        "notify.mobile_app_aaron": "notify.mobile_app_aaron_s_phone",
        "notify.mobile_app_aaron_phone": "notify.mobile_app_aaron_s_phone",
        "notify.mobile_app_aarons_phone": "notify.mobile_app_aaron_s_phone",
        "notify.mobile_app_sm_s911u1": "notify.mobile_app_amber_phone",
        "notify.mobile_app_amber": "notify.mobile_app_amber_phone",
        "notify.mobile_app_ambers_phone": "notify.mobile_app_amber_phone",
    }
    NOTIFY_RECIPIENT_ACTIONS = {
        "aaron": "notify.mobile_app_aaron_s_phone",
        "amber": "notify.mobile_app_amber_phone",
        "watch": "notify.mobile_app_aaron_s_smart_watch",
    }

    # V7 deliberately blocks system administration and security-reducing actions.
    BLOCKED_ACTION_PREFIXES = {
        "hassio.",
        "shell_command.",
        "python_script.",
        "rest_command.",
        "command_line.",
    }
    BLOCKED_ACTIONS = {
        "homeassistant.restart",
        "homeassistant.stop",
        "homeassistant.reload_all",
        "lock.unlock",
        "alarm_control_panel.alarm_disarm",
        "camera.turn_off",
        "siren.turn_off",
    }

    def __init__(
        self,
        client: HomeAssistantClient,
        database_path: str,
        *,
        enabled: bool = False,
        confirmation_ttl_seconds: int = 900,
    ) -> None:
        self.client = client
        self.database_path = Path(database_path)
        self.enabled = bool(enabled)
        self.confirmation_ttl_seconds = max(60, min(int(confirmation_ttl_seconds), 3600))
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialise_database()

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.isoformat()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def _initialise_database(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS admin_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    config_key TEXT NOT NULL,
                    name TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    previous_config_json TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    applied_at TEXT,
                    error TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_admin_proposals_conversation
                ON admin_proposals (conversation_id, status, created_at DESC);

                CREATE TABLE IF NOT EXISTS admin_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    proposal_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    config_key TEXT NOT NULL,
                    name TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    result TEXT NOT NULL,
                    details_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_admin_audit_created
                ON admin_audit (created_at DESC);
                """
            )

    @staticmethod
    def _endpoint(domain: str, config_key: str) -> str:
        if domain == "automation":
            return f"/api/config/automation/config/{config_key}"
        if domain == "script":
            return f"/api/config/script/config/{config_key}"
        raise AdminEngineError(f"Unsupported admin domain: {domain}")

    @classmethod
    def _normalise_action_block(cls, value: Any) -> Any:
        """Normalise legacy service keys while preserving control structures."""
        if isinstance(value, list):
            return [cls._normalise_action_block(item) for item in value]
        if not isinstance(value, dict):
            return value

        result = dict(value)
        if "service" in result and "action" not in result:
            result["action"] = result.pop("service")

        for key, item in list(result.items()):
            if key in {
                "sequence",
                "default",
                "then",
                "else",
                "parallel",
                "choose",
                "repeat",
            }:
                result[key] = cls._normalise_action_block(item)
        return result

    @classmethod
    def _normalise_config(cls, domain: str, config: dict[str, Any]) -> dict[str, Any]:
        result = dict(config)
        if domain == "automation":
            if "trigger" in result and "triggers" not in result:
                result["triggers"] = result.pop("trigger")
            if "condition" in result and "conditions" not in result:
                result["conditions"] = result.pop("condition")
            if "action" in result and "actions" not in result:
                result["actions"] = result.pop("action")
            result.setdefault("conditions", [])
            result["actions"] = cls._normalise_action_block(result.get("actions", []))
            result.setdefault("mode", "single")
        else:
            result["sequence"] = cls._normalise_action_block(result.get("sequence", []))
            result.setdefault("mode", "single")
        return result


    async def _available_service_actions(self) -> set[str]:
        try:
            result = await self.client.rest_request("GET", "/api/services")
        except HomeAssistantError as exc:
            raise AdminEngineError(
                f"I could not verify Home Assistant actions before saving: {exc}"
            ) from exc

        if not isinstance(result, list):
            raise AdminEngineError(
                "Home Assistant returned an invalid services response."
            )

        actions: set[str] = set()
        for domain_item in result:
            if not isinstance(domain_item, dict):
                continue
            domain = str(domain_item.get("domain") or "").strip().lower()
            services = domain_item.get("services") or {}
            if not domain or not isinstance(services, dict):
                continue
            for service in services:
                service_name = str(service).strip().lower()
                if service_name:
                    actions.add(f"{domain}.{service_name}")
        return actions

    @classmethod
    def _resolve_notify_action(
        cls,
        requested: str,
        available_actions: set[str],
        context_text: str,
    ) -> str:
        requested = requested.strip().lower()
        if requested in available_actions:
            return requested

        alias = cls.NOTIFY_ACTION_ALIASES.get(requested)
        if alias and alias in available_actions:
            return alias

        lowered_context = context_text.lower()
        requested_tail = requested.rsplit(".", 1)[-1]

        recipient: str | None = None
        if "amber" in requested_tail or "sm_s911u1" in requested_tail:
            recipient = "amber"
        elif "watch" in requested_tail:
            recipient = "watch"
        elif "aaron" in requested_tail or "sm_g996b" in requested_tail:
            recipient = "aaron"
        elif "amber" in lowered_context:
            recipient = "amber"
        elif "watch" in lowered_context:
            recipient = "watch"
        elif any(
            phrase in lowered_context
            for phrase in ("aaron", "my phone", "my mobile", "to me")
        ):
            recipient = "aaron"

        if recipient:
            resolved = cls.NOTIFY_RECIPIENT_ACTIONS[recipient]
            if resolved in available_actions:
                return resolved

        mobile_actions = sorted(
            action
            for action in available_actions
            if action.startswith("notify.mobile_app_")
        )
        if len(mobile_actions) == 1:
            return mobile_actions[0]

        available_text = ", ".join(mobile_actions) or "none"
        raise AdminEngineError(
            f"The notification action '{requested}' does not exist. "
            f"Available mobile notification actions are: {available_text}."
        )

    @classmethod
    def _canonicalise_action_services(
        cls,
        value: Any,
        available_actions: set[str],
        context_text: str,
    ) -> Any:
        if isinstance(value, list):
            return [
                cls._canonicalise_action_services(
                    item, available_actions, context_text
                )
                for item in value
            ]
        if not isinstance(value, dict):
            return value

        result = dict(value)
        action_value = result.get("action") or result.get("service")
        if isinstance(action_value, str) and "." in action_value:
            action_name = action_value.strip().lower()
            if action_name.startswith("notify.mobile_app_"):
                action_name = cls._resolve_notify_action(
                    action_name,
                    available_actions,
                    context_text,
                )
            elif action_name not in available_actions:
                raise AdminEngineError(
                    f"The Home Assistant action '{action_name}' does not exist."
                )
            result["action"] = action_name
            result.pop("service", None)

        for key, item in list(result.items()):
            if key in {"data", "target", "metadata", "variables"}:
                continue
            result[key] = cls._canonicalise_action_services(
                item,
                available_actions,
                context_text,
            )
        return result

    async def _canonicalise_config_actions(
        self,
        domain: str,
        config: dict[str, Any],
        context_text: str,
    ) -> dict[str, Any]:
        available_actions = await self._available_service_actions()
        result = dict(config)
        if domain == "automation":
            result["actions"] = self._canonicalise_action_services(
                result.get("actions", []),
                available_actions,
                context_text,
            )
        else:
            result["sequence"] = self._canonicalise_action_services(
                result.get("sequence", []),
                available_actions,
                context_text,
            )
        return result

    @classmethod
    def _validate_shape(
        cls,
        domain: str,
        operation: str,
        config_key: str,
        config: dict[str, Any],
    ) -> None:
        if domain not in cls.ALLOWED_DOMAINS:
            raise AdminEngineError("Only automations and scripts can be managed.")
        if operation not in cls.ALLOWED_OPERATIONS:
            raise AdminEngineError("Only create and update operations are supported.")
        if not cls.SAFE_KEY_PATTERN.fullmatch(config_key):
            raise AdminEngineError(
                "The automation or script key must contain only lowercase letters, numbers and underscores."
            )
        if not config_key.startswith("jarvis_") and operation == "create":
            raise AdminEngineError("New Jarvis items must use a key beginning with 'jarvis_'.")
        if not isinstance(config, dict):
            raise AdminEngineError("The proposed Home Assistant configuration must be an object.")

        if domain == "automation":
            if not str(config.get("alias") or "").strip():
                raise AdminEngineError("An automation requires an alias.")
            if not config.get("triggers"):
                raise AdminEngineError("An automation requires at least one trigger.")
            if not config.get("actions"):
                raise AdminEngineError("An automation requires at least one action.")
        else:
            if not config.get("sequence"):
                raise AdminEngineError("A script requires at least one sequence action.")

    @classmethod
    def _walk_actions(cls, value: Any) -> list[str]:
        found: list[str] = []
        if isinstance(value, list):
            for item in value:
                found.extend(cls._walk_actions(item))
            return found
        if not isinstance(value, dict):
            return found

        action_value = value.get("action") or value.get("service")
        if isinstance(action_value, str):
            found.append(action_value.strip().lower())

        for key, item in value.items():
            if key in {"data", "target", "metadata", "variables"}:
                continue
            found.extend(cls._walk_actions(item))
        return found

    @classmethod
    def _check_blocked_actions(cls, domain: str, config: dict[str, Any]) -> None:
        action_block = config.get("actions") if domain == "automation" else config.get("sequence")
        for action in cls._walk_actions(action_block):
            if action in cls.BLOCKED_ACTIONS or any(
                action.startswith(prefix) for prefix in cls.BLOCKED_ACTION_PREFIXES
            ):
                raise AdminEngineError(
                    f"Admin Mode v7 blocks the high-risk action '{action}'."
                )

    async def check_access(self) -> dict[str, Any]:
        if not self.enabled:
            return {
                "enabled": False,
                "admin_access": False,
                "message": "Jarvis Admin Mode is disabled.",
            }
        try:
            await self.client.rest_request(
                "GET",
                self._endpoint("automation", "__jarvis_access_check__"),
            )
        except HomeAssistantHTTPError as exc:
            if exc.status_code == 404:
                return {
                    "enabled": True,
                    "admin_access": True,
                    "message": "Home Assistant admin API access is available.",
                }
            if exc.status_code in {401, 403}:
                return {
                    "enabled": True,
                    "admin_access": False,
                    "message": "The configured Home Assistant token is not an administrator token.",
                }
            return {
                "enabled": True,
                "admin_access": False,
                "message": str(exc),
            }
        except HomeAssistantError as exc:
            return {
                "enabled": True,
                "admin_access": False,
                "message": str(exc),
            }
        return {
            "enabled": True,
            "admin_access": True,
            "message": "Home Assistant admin API access is available.",
        }

    async def list_items(
        self,
        domain: str,
        query: str = "",
        limit: int = 30,
    ) -> dict[str, Any]:
        if domain not in self.ALLOWED_DOMAINS:
            raise AdminEngineError("Only automations and scripts can be listed.")
        states = await self.client.get_states()
        query_terms = [term for term in re.split(r"\W+", query.lower()) if term]
        items: list[dict[str, Any]] = []
        for state in states:
            entity_id = str(state.get("entity_id") or "")
            if not entity_id.startswith(f"{domain}."):
                continue
            attributes = state.get("attributes") or {}
            name = str(attributes.get("friendly_name") or entity_id)
            config_key = (
                str(attributes.get("id") or "")
                if domain == "automation"
                else entity_id.split(".", 1)[1]
            )
            haystack = f"{entity_id} {name} {config_key}".lower()
            if query_terms and not all(term in haystack for term in query_terms):
                continue
            items.append(
                {
                    "domain": domain,
                    "entity_id": entity_id,
                    "config_key": config_key,
                    "name": name,
                    "state": state.get("state"),
                    "last_triggered": attributes.get("last_triggered"),
                    "mode": attributes.get("mode"),
                    "current": attributes.get("current"),
                }
            )
        items.sort(key=lambda item: (item["name"].lower(), item["entity_id"]))
        safe_limit = max(1, min(int(limit), 100))
        return {
            "success": True,
            "domain": domain,
            "query": query,
            "count": len(items[:safe_limit]),
            "items": items[:safe_limit],
        }

    async def get_config(self, domain: str, config_key: str) -> dict[str, Any] | None:
        try:
            result = await self.client.rest_request(
                "GET",
                self._endpoint(domain, config_key),
            )
        except HomeAssistantHTTPError as exc:
            if exc.status_code == 404:
                return None
            raise AdminEngineError(str(exc)) from exc
        except HomeAssistantError as exc:
            raise AdminEngineError(str(exc)) from exc
        if not isinstance(result, dict):
            raise AdminEngineError("Home Assistant returned an invalid configuration response.")
        return result

    async def validate_runnable_item(
        self,
        domain: str,
        config_key: str,
    ) -> dict[str, Any]:
        """Inspect a routine before Jarvis is allowed to run it."""
        if domain not in self.ALLOWED_DOMAINS:
            raise AdminEngineError("Only automations and scripts can be run.")

        config = await self.get_config(domain, config_key)
        if config is None:
            raise AdminEngineError(
                "I can only run that routine after Home Assistant exposes its "
                "configuration for a safety check."
            )

        normalised = self._normalise_config(domain, config)
        self._check_blocked_actions(domain, normalised)
        return normalised

    @staticmethod
    def _is_validate_schema_mismatch(message: str) -> bool:
        lowered = message.lower()
        return (
            "extra keys not allowed" in lowered
            or "required key not provided" in lowered
            or "unknown command" in lowered
        )

    @staticmethod
    def _local_action_validation(actions: Any) -> dict[str, Any]:
        """Perform conservative structural checks when HA exposes no action validator."""
        if not isinstance(actions, list) or not actions:
            raise AdminEngineError("The proposed action sequence must be a non-empty list.")

        structural_keys = {
            "action",
            "service",
            "delay",
            "wait_template",
            "wait_for_trigger",
            "choose",
            "if",
            "repeat",
            "parallel",
            "sequence",
            "stop",
            "event",
            "variables",
            "condition",
            "device_id",
            "scene",
        }
        for index, item in enumerate(actions):
            if not isinstance(item, dict) or not item:
                raise AdminEngineError(
                    f"Action {index + 1} must be a non-empty Home Assistant action object."
                )
            if not any(key in item for key in structural_keys):
                raise AdminEngineError(
                    f"Action {index + 1} does not contain a recognised Home Assistant action key."
                )

        return {
            "valid": True,
            "error": None,
            "validation": "local_structure_and_authoritative_save",
        }

    @staticmethod
    def _raise_invalid_validation(result: Any) -> None:
        if not isinstance(result, dict):
            raise AdminEngineError("Home Assistant returned an invalid validation response.")

        invalid: list[str] = []
        for section, section_result in result.items():
            if isinstance(section_result, dict) and not section_result.get("valid", False):
                invalid.append(f"{section}: {section_result.get('error') or 'invalid'}")
        if invalid:
            raise AdminEngineError("The proposed configuration is invalid: " + "; ".join(invalid))

    async def _validate_action_block(self, actions: list[dict[str, Any]]) -> dict[str, Any]:
        """Validate actions across Home Assistant API schema variants."""
        errors: list[str] = []
        for key in ("action", "actions"):
            try:
                result = await self.client.send_command(
                    {"type": "validate_config", key: actions}
                )
            except HomeAssistantError as exc:
                message = str(exc)
                if self._is_validate_schema_mismatch(message):
                    errors.append(message)
                    continue
                raise AdminEngineError(
                    f"Home Assistant action validation failed: {message}"
                ) from exc

            self._raise_invalid_validation(result)
            return result

        # Some Home Assistant builds removed action validation from this WebSocket
        # command. The configuration REST endpoint still performs authoritative,
        # atomic validation when the user confirms the proposal. We therefore keep
        # a strict local shape check here and defer final semantic validation to save.
        local = self._local_action_validation(actions)
        local["compatibility_note"] = "; ".join(errors[-2:])
        return {"action": local}

    async def validate_config(
        self,
        domain: str,
        config_key: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}

        if domain == "automation":
            command: dict[str, Any] = {"type": "validate_config"}
            command["trigger"] = config.get("triggers", [])
            command["condition"] = config.get("conditions", [])
            try:
                automation_result = await self.client.send_command(command)
            except HomeAssistantError as exc:
                raise AdminEngineError(
                    f"Home Assistant trigger/condition validation failed: {exc}"
                ) from exc
            self._raise_invalid_validation(automation_result)
            result.update(automation_result)
            actions = config.get("actions", [])
        else:
            actions = config.get("sequence", [])

        action_result = await self._validate_action_block(actions)
        result.update(action_result)
        return result

    def _cancel_previous_sync(self, conversation_id: str, now: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE admin_proposals
                SET status = 'cancelled', error = 'Replaced by a newer proposal'
                WHERE conversation_id = ? AND status = 'pending'
                """,
                (conversation_id,),
            )

    def _store_proposal_sync(self, proposal: PendingAdminChange) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO admin_proposals (
                    proposal_id, conversation_id, domain, operation, config_key,
                    name, summary, config_json, previous_config_json, status,
                    created_at, expires_at, applied_at, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal.proposal_id,
                    proposal.conversation_id,
                    proposal.domain,
                    proposal.operation,
                    proposal.config_key,
                    proposal.name,
                    proposal.summary,
                    proposal.config_json,
                    proposal.previous_config_json,
                    proposal.status,
                    proposal.created_at,
                    proposal.expires_at,
                    proposal.applied_at,
                    proposal.error,
                ),
            )

    async def propose_change(
        self,
        *,
        conversation_id: str,
        domain: str,
        operation: str,
        config_key: str,
        name: str,
        summary: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.enabled:
            raise AdminEngineError(
                "Jarvis Admin Mode is disabled. Set JARVIS_ADMIN_MODE_ENABLED=true and rebuild Jarvis."
            )

        domain = domain.strip().lower()
        operation = operation.strip().lower()
        config_key = config_key.strip().lower()
        name = re.sub(r"\s+", " ", name).strip()
        summary = re.sub(r"\s+", " ", summary).strip()
        if not name or len(name) > 120:
            raise AdminEngineError("The proposed item needs a short name.")
        if not summary or len(summary) > 500:
            raise AdminEngineError("The proposed change needs a concise summary.")
        config = self._normalise_config(domain, config)
        config = await self._canonicalise_config_actions(
            domain,
            config,
            f"{name} {summary}",
        )
        self._validate_shape(domain, operation, config_key, config)
        self._check_blocked_actions(domain, config)

        existing = await self.get_config(domain, config_key)
        if operation == "create" and existing is not None:
            raise AdminEngineError(
                f"A {domain} with key '{config_key}' already exists. Use update instead."
            )
        if operation == "update" and existing is None:
            raise AdminEngineError(
                f"I could not find a {domain} with key '{config_key}' to update."
            )

        await self.validate_config(domain, config_key, config)

        now = self._utc_now()
        expires = now + timedelta(seconds=self.confirmation_ttl_seconds)
        proposal = PendingAdminChange(
            proposal_id=uuid.uuid4().hex,
            conversation_id=conversation_id,
            domain=domain,
            operation=operation,
            config_key=config_key,
            name=name or str(config.get("alias") or config_key),
            summary=summary,
            config_json=json.dumps(config, ensure_ascii=False, sort_keys=True),
            previous_config_json=(
                json.dumps(existing, ensure_ascii=False, sort_keys=True)
                if existing is not None
                else None
            ),
            status="pending",
            created_at=self._iso(now),
            expires_at=self._iso(expires),
            applied_at=None,
            error=None,
        )
        await asyncio.to_thread(self._cancel_previous_sync, conversation_id, self._iso(now))
        await asyncio.to_thread(self._store_proposal_sync, proposal)

        action_word = "create" if operation == "create" else "update"
        return {
            "success": True,
            "proposal": asdict(proposal),
            "requires_confirmation": True,
            "response_message": (
                f"I’m ready to {action_word} {proposal.name}. {summary} "
                "Say ‘confirm’ to apply it, or ‘cancel’ to discard it."
            ),
        }

    def _get_pending_sync(self, conversation_id: str) -> PendingAdminChange | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM admin_proposals
                WHERE conversation_id = ? AND status = 'pending'
                ORDER BY created_at DESC LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        proposal = PendingAdminChange(**dict(row))
        if datetime.fromisoformat(proposal.expires_at) <= self._utc_now():
            self._mark_status_sync(proposal.proposal_id, "expired", "Confirmation expired")
            return None
        return proposal

    async def get_pending(self, conversation_id: str) -> dict[str, Any] | None:
        proposal = await asyncio.to_thread(self._get_pending_sync, conversation_id)
        return asdict(proposal) if proposal else None

    def _mark_status_sync(
        self,
        proposal_id: str,
        status: str,
        error: str | None = None,
        applied_at: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE admin_proposals
                SET status = ?, error = ?, applied_at = ?
                WHERE proposal_id = ?
                """,
                (status, error, applied_at, proposal_id),
            )

    def _audit_sync(
        self,
        proposal: PendingAdminChange,
        result: str,
        details: dict[str, Any],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO admin_audit (
                    proposal_id, conversation_id, domain, operation, config_key,
                    name, summary, result, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal.proposal_id,
                    proposal.conversation_id,
                    proposal.domain,
                    proposal.operation,
                    proposal.config_key,
                    proposal.name,
                    proposal.summary,
                    result,
                    json.dumps(details, ensure_ascii=False, default=str),
                    self._iso(self._utc_now()),
                ),
            )

    async def cancel_pending(self, conversation_id: str) -> dict[str, Any]:
        proposal_data = await self.get_pending(conversation_id)
        if proposal_data is None:
            return {
                "success": False,
                "response_message": "There is no pending Home Assistant change to cancel.",
            }
        proposal = PendingAdminChange(**proposal_data)
        await asyncio.to_thread(
            self._mark_status_sync,
            proposal.proposal_id,
            "cancelled",
            "Cancelled by Aaron",
            None,
        )
        await asyncio.to_thread(self._audit_sync, proposal, "cancelled", {})
        return {
            "success": True,
            "proposal_id": proposal.proposal_id,
            "response_message": f"Cancelled the proposed change to {proposal.name}.",
        }

    async def _rollback(
        self,
        proposal: PendingAdminChange,
        previous: dict[str, Any] | None,
    ) -> None:
        endpoint = self._endpoint(proposal.domain, proposal.config_key)
        if previous is None:
            try:
                await self.client.rest_request("DELETE", endpoint)
            except HomeAssistantError:
                pass
            return
        await self.client.rest_request("POST", endpoint, json_data=previous)

    async def apply_pending(self, conversation_id: str) -> dict[str, Any]:
        if not self.enabled:
            return {
                "success": False,
                "response_message": "Jarvis Admin Mode is disabled.",
            }
        proposal_data = await self.get_pending(conversation_id)
        if proposal_data is None:
            return {
                "success": False,
                "response_message": "There is no pending Home Assistant change to confirm.",
            }
        proposal = PendingAdminChange(**proposal_data)
        config = self._normalise_config(
            proposal.domain,
            json.loads(proposal.config_json),
        )
        config = await self._canonicalise_config_actions(
            proposal.domain,
            config,
            f"{proposal.name} {proposal.summary}",
        )
        self._validate_shape(
            proposal.domain,
            proposal.operation,
            proposal.config_key,
            config,
        )
        self._check_blocked_actions(proposal.domain, config)
        await self.validate_config(
            proposal.domain,
            proposal.config_key,
            config,
        )
        previous = (
            json.loads(proposal.previous_config_json)
            if proposal.previous_config_json
            else None
        )
        endpoint = self._endpoint(proposal.domain, proposal.config_key)

        try:
            await self.client.rest_request("POST", endpoint, json_data=config)
            await asyncio.sleep(0.8)
            stored = await self.get_config(proposal.domain, proposal.config_key)
            if stored is None:
                raise AdminEngineError("Home Assistant did not return the saved configuration.")
            expected_name = str(config.get("alias") or proposal.name).strip().lower()
            stored_name = str(stored.get("alias") or proposal.name).strip().lower()
            if expected_name and stored_name != expected_name:
                raise AdminEngineError("The saved configuration could not be verified.")
        except Exception as exc:
            try:
                await self._rollback(proposal, previous)
            except Exception:
                pass
            message = str(exc)
            await asyncio.to_thread(
                self._mark_status_sync,
                proposal.proposal_id,
                "failed",
                message,
                None,
            )
            await asyncio.to_thread(
                self._audit_sync,
                proposal,
                "failed",
                {"error": message, "rollback_attempted": True},
            )
            return {
                "success": False,
                "proposal_id": proposal.proposal_id,
                "response_message": (
                    f"I couldn’t apply the change to {proposal.name}. The previous configuration was preserved. {message}"
                ),
            }

        runtime_loaded = False
        for delay in (0.4, 0.8, 1.2):
            await asyncio.sleep(delay)
            items = await self.list_items(proposal.domain, proposal.config_key, 20)
            if any(
                str(item.get("config_key") or "") == proposal.config_key
                for item in items.get("items", [])
            ):
                runtime_loaded = True
                break

        applied_at = self._iso(self._utc_now())
        await asyncio.to_thread(
            self._mark_status_sync,
            proposal.proposal_id,
            "applied",
            None,
            applied_at,
        )
        await asyncio.to_thread(
            self._audit_sync,
            proposal,
            "applied",
            {"stored_verified": True, "runtime_loaded": runtime_loaded},
        )
        verb = "Created" if proposal.operation == "create" else "Updated"
        response_message = (
            f"{verb}, validated and loaded {proposal.name}."
            if runtime_loaded
            else (
                f"{verb} and validated {proposal.name}. Home Assistant is still "
                "finishing the reload."
            )
        )
        return {
            "success": True,
            "proposal_id": proposal.proposal_id,
            "domain": proposal.domain,
            "config_key": proposal.config_key,
            "runtime_loaded": runtime_loaded,
            "response_message": response_message,
        }

    def _audit_list_sync(self, limit: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT audit_id, proposal_id, conversation_id, domain, operation,
                       config_key, name, summary, result, created_at
                FROM admin_audit
                ORDER BY audit_id DESC LIMIT ?
                """,
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [dict(row) for row in rows]

    async def audit_log(self, limit: int = 50) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._audit_list_sync, limit)
