from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Header, HTTPException
from pydantic import BaseModel, Field

from app import main as core
from app.proactive_orchestrator import ProactiveOrchestrator


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on", "enabled"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (AttributeError, ValueError):
        return default


def _env_text(name: str, default: str) -> str:
    return os.getenv(name, default).strip() or default


# House Awareness remains the event recorder. v15 becomes the only automatic
# delivery path so an event cannot be announced twice by the legacy v13 logic.
core.awareness.proactive_enabled = False

proactive = ProactiveOrchestrator(
    awareness=core.awareness,
    tools=core.tools,
    database_path="/app/data/jarvis_proactive.db",
    enabled=_env_bool("JARVIS_PROACTIVE_ENABLED", True),
    announcement_target=_env_text("JARVIS_PROACTIVE_TARGET", "living_room"),
    timezone_name=_env_text("JARVIS_TIMEZONE", "Europe/London"),
    quiet_start=_env_text("JARVIS_PROACTIVE_QUIET_START", "22:30"),
    quiet_end=_env_text("JARVIS_PROACTIVE_QUIET_END", "07:00"),
    poll_seconds=_env_int("JARVIS_PROACTIVE_POLL_SECONDS", 5),
    duplicate_cooldown_seconds=_env_int(
        "JARVIS_PROACTIVE_COOLDOWN_SECONDS",
        300,
    ),
    opening_delay_seconds=_env_int("JARVIS_PROACTIVE_OPENING_DELAY_SECONDS", 300),
    camera_offline_seconds=_env_int("JARVIS_PROACTIVE_CAMERA_OFFLINE_SECONDS", 120),
    camera_scan_seconds=_env_int("JARVIS_PROACTIVE_CAMERA_SCAN_SECONDS", 30),
    escalation_seconds=_env_int("JARVIS_PROACTIVE_ESCALATION_SECONDS", 300),
    max_escalations=_env_int("JARVIS_PROACTIVE_MAX_ESCALATIONS", 2),
    process_existing_events=_env_bool(
        "JARVIS_PROACTIVE_PROCESS_EXISTING_EVENTS",
        False,
    ),
)

app = core.app
app.version = "2.2.0"

_original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _v15_lifespan(application: Any):
    async with _original_lifespan(application):
        await proactive.start()
        try:
            yield
        finally:
            await proactive.stop()


app.router.lifespan_context = _v15_lifespan

_original_execute_ai_request = core._execute_ai_request


async def _execute_ai_request_v15(
    request: core.TextCommandRequest,
    on_text_delta: Any = None,
) -> dict[str, object]:
    """Handle replies to proactive alerts before the normal AI pipeline."""

    request_started = time.monotonic()
    actor = core.UserContext.from_request(
        user_id=request.user_id,
        user_name=request.user_name,
        user_is_admin=request.user_is_admin,
        device_id=request.device_id,
        voice_mode=request.voice_mode,
    )
    command = await proactive.handle_command(request.text, actor)
    if not command.handled:
        return await _original_execute_ai_request(
            request,
            on_text_delta=on_text_delta,
        )

    external_id, storage_id = core._conversation_scope(
        request.conversation_id,
        actor.user_key,
    )
    conversation = await core.conversations.ensure_conversation(
        conversation_id=storage_id,
        source=f"home_assistant:{actor.user_key}",
    )
    storage_id = str(conversation["conversation_id"])
    await core.conversations.add_user_message(
        conversation_id=storage_id,
        content=request.text,
    )
    await core.conversations.add_assistant_message(
        conversation_id=storage_id,
        content=command.response,
    )
    if on_text_delta is not None and command.response:
        await on_text_delta(command.response)

    result: dict[str, object] = {
        "success": command.success,
        "response": command.response,
        "model": "proactive-orchestrator-v15",
        "intent": command.intent,
        "deterministic": True,
        "tool_called": bool(command.details),
        "tool_rounds": 1 if command.details else 0,
        "calls": [],
        "memory_used": False,
        "conversation_id": external_id,
        "message_count": await core.conversations.message_count(storage_id),
        "user": {
            "key": actor.user_key,
            "name": actor.display_name,
            "is_admin": actor.is_admin,
        },
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
        },
        "timings": {
            "jarvis_request_total_ms": round(
                (time.monotonic() - request_started) * 1000
            )
        },
    }
    if command.details is not None:
        result["proactive"] = command.details

    try:
        await core.improvement.observe_interaction(
            conversation_id=storage_id,
            actor=actor,
            raw_text=request.text,
            result=result,
        )
    except Exception:
        core.logger.exception(
            "Could not record proactive command as improvement evidence"
        )
    return result


# Existing FastAPI endpoints resolve this global at request time, including the
# streaming endpoint, so no duplicate route definitions are required.
core._execute_ai_request = _execute_ai_request_v15


class ProactiveSnoozeRequest(BaseModel):
    seconds: int = Field(ge=60, le=604800)
    actor: str = Field(default="api", min_length=1, max_length=100)


class ProactiveAcknowledgeRequest(BaseModel):
    actor: str = Field(default="api", min_length=1, max_length=100)


@app.get("/api/proactive/status")
async def proactive_status() -> dict[str, Any]:
    return await proactive.status()


@app.get("/api/proactive/alerts")
async def proactive_alerts(
    limit: int = 50,
    status: str | None = None,
) -> dict[str, Any]:
    statuses = {status} if status else None
    items = await proactive.list_alerts(limit=limit, statuses=statuses)
    return {"count": len(items), "alerts": items}


@app.get("/api/proactive/audit")
async def proactive_audit(limit: int = 100) -> dict[str, Any]:
    items = await proactive.audit_log(limit=limit)
    return {"count": len(items), "items": items}


@app.post("/api/proactive/process")
async def proactive_process(
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    core._require_improvement_token(x_jarvis_admin_token)
    await proactive.process_once()
    return {"success": True, "status": await proactive.status()}


@app.post("/api/proactive/alerts/{alert_id}/acknowledge")
async def proactive_acknowledge(
    alert_id: int,
    request: ProactiveAcknowledgeRequest,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    core._require_improvement_token(x_jarvis_admin_token)
    updated = await proactive.acknowledge(alert_id, request.actor)
    if not updated:
        raise HTTPException(status_code=404, detail="Active proactive alert not found.")
    return {"success": True, "alert_id": alert_id}


@app.post("/api/proactive/alerts/{alert_id}/snooze")
async def proactive_snooze(
    alert_id: int,
    request: ProactiveSnoozeRequest,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    core._require_improvement_token(x_jarvis_admin_token)
    updated = await proactive.snooze(alert_id, request.seconds, request.actor)
    if not updated:
        raise HTTPException(status_code=404, detail="Proactive alert not found.")
    return {
        "success": True,
        "alert_id": alert_id,
        "seconds": request.seconds,
    }
