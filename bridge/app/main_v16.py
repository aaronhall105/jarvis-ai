from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Header, HTTPException, WebSocket
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import main_v15 as v15
from app.capability_grounding import CapabilityGroundingEngine
from app.task_engine import TemporalActionEngine
from app.recurring_schedule_engine import RecurringScheduleEngine
from app.conditional_action_engine import ConditionalActionEngine
from app.routine_engine import RoutineEngine
from app.voice_session_engine import VoiceSessionEngine
from app.realtime_voice import RealtimeVoiceProxy

core = v15.core


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


tasks = TemporalActionEngine(
    tools=core.tools,
    database_path="/app/data/jarvis_tasks.db",
    enabled=_env_bool("JARVIS_TASKS_ENABLED", True),
    timezone_name=_env_text("JARVIS_TIMEZONE", "Europe/London"),
    poll_seconds=_env_int("JARVIS_TASKS_POLL_SECONDS", 1),
    max_future_days=_env_int("JARVIS_TASKS_MAX_FUTURE_DAYS", 365),
    notify_completion=_env_bool("JARVIS_TASKS_NOTIFY_COMPLETION", True),
)
capabilities = CapabilityGroundingEngine(core.tools)
schedules = RecurringScheduleEngine(
    tools=core.tools,
    action_engine=tasks,
    database_path="/app/data/jarvis_recurring_schedules.db",
    enabled=_env_bool("JARVIS_SCHEDULES_ENABLED", True),
    timezone_name=_env_text("JARVIS_TIMEZONE", "Europe/London"),
    poll_seconds=_env_int("JARVIS_SCHEDULES_POLL_SECONDS", 1),
    misfire_grace_seconds=_env_int(
        "JARVIS_SCHEDULES_MISFIRE_GRACE_SECONDS",
        300,
    ),
    notify_completion=_env_bool(
        "JARVIS_SCHEDULES_NOTIFY_COMPLETION",
        True,
    ),
)

_original_task_handle_command = tasks.handle_command

async def _handle_temporal_or_recurring_command(text: str, actor: Any):
    schedule_command = await schedules.handle_command(text, actor)
    if schedule_command.handled:
        return schedule_command
    return await _original_task_handle_command(text, actor)

tasks.handle_command = _handle_temporal_or_recurring_command

conditions = ConditionalActionEngine(
    tools=core.tools,
    action_engine=tasks,
    database_path="/app/data/jarvis_conditional_actions.db",
    enabled=_env_bool("JARVIS_CONDITIONS_ENABLED", True),
    timezone_name=_env_text("JARVIS_TIMEZONE", "Europe/London"),
    poll_seconds=_env_int("JARVIS_CONDITIONS_POLL_SECONDS", 2),
    default_cooldown_seconds=_env_int(
        "JARVIS_CONDITIONS_DEFAULT_COOLDOWN_SECONDS",
        300,
    ),
    default_debounce_seconds=_env_int(
        "JARVIS_CONDITIONS_DEFAULT_DEBOUNCE_SECONDS",
        2,
    ),
    notify_failures=_env_bool("JARVIS_CONDITIONS_NOTIFY_FAILURES", True),
)

_original_temporal_or_recurring_handle_command = tasks.handle_command

async def _handle_conditional_or_existing_command(text: str, actor: Any):
    conditional_command = await conditions.handle_command(text, actor)
    if conditional_command.handled:
        return conditional_command
    return await _original_temporal_or_recurring_handle_command(text, actor)

tasks.handle_command = _handle_conditional_or_existing_command

routines = RoutineEngine(
    action_engine=tasks,
    database_path="/app/data/jarvis_routines.db",
    enabled=_env_bool("JARVIS_ROUTINES_ENABLED", True),
    max_steps=_env_int("JARVIS_ROUTINES_MAX_STEPS", 8),
)

voice_sessions = VoiceSessionEngine(
    database_path="/app/data/jarvis_voice_sessions.db",
    idle_timeout_seconds=_env_int("JARVIS_VOICE_SESSION_IDLE_SECONDS", 45),
    max_session_seconds=_env_int("JARVIS_VOICE_SESSION_MAX_SECONDS", 300),
)

realtime_voice = RealtimeVoiceProxy.from_environment()

_original_conditional_or_existing_handle_command = tasks.handle_command

async def _handle_routine_or_existing_command(text: str, actor: Any):
    routine_command = await routines.handle_command(text, actor)
    if routine_command.handled:
        return routine_command
    return await _original_conditional_or_existing_handle_command(text, actor)

tasks.handle_command = _handle_routine_or_existing_command

app = v15.app
app.version = "2.9.0"

_original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _v16_lifespan(application: Any):
    async with _original_lifespan(application):
        await tasks.start()
        await schedules.start()
        await conditions.start()
        try:
            yield
        finally:
            await conditions.stop()
            await schedules.stop()
            await tasks.stop()


app.router.lifespan_context = _v16_lifespan

_original_execute_ai_request = core._execute_ai_request


async def _execute_ai_request_v16(
    request: core.TextCommandRequest,
    on_text_delta: Any = None,
) -> dict[str, object]:
    """Handle temporal commands before proactive and normal AI routing."""

    request_started = time.monotonic()
    actor = core.UserContext.from_request(
        user_id=request.user_id,
        user_name=request.user_name,
        user_is_admin=request.user_is_admin,
        device_id=request.device_id,
        voice_mode=request.voice_mode,
    )
    if request.voice_mode and request.voice_session_id:
        await voice_sessions.touch(
            session_id=request.voice_session_id,
            conversation_id=(request.conversation_id or request.voice_session_id),
            user_key=actor.user_key,
            satellite_id=request.satellite_id,
            device_id=request.device_id,
            endpoint_kind=request.voice_endpoint_kind,
            turn_index=request.voice_session_turn,
        )
    command = await tasks.handle_command(request.text, actor)
    if not command.handled:
        external_id, storage_id = core._conversation_scope(
            request.conversation_id,
            actor.user_key,
        )
        history = await core.conversations.get_messages(
            conversation_id=storage_id,
            limit=12,
        )
        capability_command = await capabilities.handle(
            text=request.text,
            history=history,
            actor=actor,
        )
        if not capability_command.handled:
            return await _original_execute_ai_request(
                request,
                on_text_delta=on_text_delta,
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
            content=capability_command.response,
        )
        if on_text_delta is not None and capability_command.response:
            await on_text_delta(capability_command.response)

        capability_result: dict[str, object] = {
            "success": capability_command.success,
            "response": capability_command.response,
            "model": "capability-grounding-v16.0.7",
            "intent": capability_command.intent,
            "deterministic": True,
            "tool_called": bool(capability_command.calls),
            "tool_rounds": 1 if capability_command.calls else 0,
            "calls": list(capability_command.calls),
            "memory_used": False,
            "continue_conversation": capability_command.continue_conversation,
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
        if capability_command.details is not None:
            capability_result["capability"] = capability_command.details
        try:
            await core.improvement.observe_interaction(
                conversation_id=storage_id,
                actor=actor,
                raw_text=request.text,
                result=capability_result,
            )
        except Exception:
            core.logger.exception(
                "Could not record capability-grounded command as improvement evidence"
            )
        return capability_result

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
        "model": (
            "routine-engine-v16.3.0"
            if command.intent.startswith(("routine", "scene"))
            else (
                "conditional-action-engine-v16.2.0"
                if command.intent.startswith("condition")
                else "temporal-action-engine-v16.3.0"
            )
        ),
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
        if command.intent.startswith(("routine", "scene")):
            result["routine"] = command.details
        elif command.intent.startswith("condition"):
            result["condition"] = command.details
        elif command.intent.startswith("schedule"):
            result["schedule"] = command.details
        else:
            result["task"] = command.details

    try:
        await core.improvement.observe_interaction(
            conversation_id=storage_id,
            actor=actor,
            raw_text=request.text,
            result=result,
        )
    except Exception:
        core.logger.exception(
            "Could not record temporal command as improvement evidence"
        )
    return result


# Existing text and streaming endpoints resolve this global at request time.
core._execute_ai_request = _execute_ai_request_v16


class TaskCancelRequest(BaseModel):
    actor: str = Field(default="api", min_length=1, max_length=100)
    owner_key: str | None = Field(default=None, max_length=100)




_ASSIST_UPDATE_PATH = (
    Path(__file__).resolve().parent
    / "assets"
    / "jarvis-assist-smart-audio-gate-v1.5.4.tar.gz"
)


@app.get(
    "/api/updates/jarvis-assist-smart-audio-gate-v1.5.4.tar.gz",
    include_in_schema=False,
)
async def assist_spoken_progress_update() -> FileResponse:
    if not _ASSIST_UPDATE_PATH.is_file():
        raise HTTPException(status_code=404, detail="Assist update package not found.")
    return FileResponse(
        _ASSIST_UPDATE_PATH,
        media_type="application/gzip",
        filename="jarvis-assist-smart-audio-gate-v1.5.4.tar.gz",
    )


@app.get("/api/tasks/status")
async def task_status() -> dict[str, Any]:
    return await tasks.status()


@app.get("/api/tasks")
async def task_list(
    owner_key: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    statuses = {status} if status else None
    items = await tasks.list_tasks(
        owner_key=owner_key,
        statuses=statuses,
        limit=limit,
    )
    return {"count": len(items), "tasks": items}


@app.get("/api/tasks/{task_id}")
async def task_get(task_id: int) -> dict[str, Any]:
    task = await tasks.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Scheduled task not found.")
    return task


@app.post("/api/tasks/process")
async def task_process(
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    core._require_improvement_token(x_jarvis_admin_token)
    count = await tasks.process_once()
    return {"success": True, "processed": count, "status": await tasks.status()}


@app.post("/api/tasks/{task_id}/cancel")
async def task_cancel(
    task_id: int,
    request: TaskCancelRequest,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    core._require_improvement_token(x_jarvis_admin_token)
    updated = await tasks.cancel_task(
        task_id,
        owner_key=request.owner_key,
        actor=request.actor,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Pending scheduled task not found.")
    return {"success": True, "task_id": task_id}


# Jarvis v16.1.0 recurring schedule API

@app.get("/api/schedules/status")
async def schedule_status() -> dict[str, Any]:
    return await schedules.status()


@app.get("/api/schedules")
async def schedule_list(
    owner_key: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    statuses = {status} if status else None
    items = await schedules.list_schedules(
        owner_key=owner_key,
        statuses=statuses,
        limit=limit,
    )
    return {"count": len(items), "schedules": items}


@app.get("/api/schedules/{schedule_id}")
async def schedule_get(schedule_id: int) -> dict[str, Any]:
    item = await schedules.get_schedule(schedule_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Recurring schedule not found.")
    return item


@app.get("/api/schedules/{schedule_id}/runs")
async def schedule_runs(
    schedule_id: int,
    owner_key: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    items = await schedules.list_runs(
        schedule_id,
        owner_key=owner_key,
        limit=limit,
    )
    return {"count": len(items), "runs": items}


@app.post("/api/schedules/process")
async def schedule_process(
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    core._require_improvement_token(x_jarvis_admin_token)
    count = await schedules.process_once()
    return {"success": True, "processed": count, "status": await schedules.status()}


@app.post("/api/schedules/{schedule_id}/pause")
async def schedule_pause(
    schedule_id: int,
    request: TaskCancelRequest,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    core._require_improvement_token(x_jarvis_admin_token)
    updated = await schedules.pause_schedule(
        schedule_id,
        owner_key=request.owner_key,
        actor=request.actor,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Active recurring schedule not found.")
    return {"success": True, "schedule_id": schedule_id}


@app.post("/api/schedules/{schedule_id}/resume")
async def schedule_resume(
    schedule_id: int,
    request: TaskCancelRequest,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    core._require_improvement_token(x_jarvis_admin_token)
    updated = await schedules.resume_schedule(
        schedule_id,
        owner_key=request.owner_key,
        actor=request.actor,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Paused recurring schedule not found.")
    return {"success": True, "schedule_id": schedule_id}


@app.post("/api/schedules/{schedule_id}/cancel")
async def schedule_cancel(
    schedule_id: int,
    request: TaskCancelRequest,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    core._require_improvement_token(x_jarvis_admin_token)
    updated = await schedules.cancel_schedule(
        schedule_id,
        owner_key=request.owner_key,
        actor=request.actor,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Recurring schedule not found.")
    return {"success": True, "schedule_id": schedule_id}


# Jarvis v16.2.0 conditional action API


@app.get("/api/conditions/status")
async def condition_status() -> dict[str, Any]:
    return await conditions.status()


@app.get("/api/conditions")
async def condition_list(
    owner_key: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    statuses = {status} if status else None
    items = await conditions.list_rules(
        owner_key=owner_key,
        statuses=statuses,
        limit=limit,
    )
    return {"count": len(items), "rules": items}


@app.get("/api/conditions/{rule_id}")
async def condition_get(rule_id: int) -> dict[str, Any]:
    item = await conditions.get_rule(rule_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Conditional rule not found.")
    return item


@app.get("/api/conditions/{rule_id}/runs")
async def condition_runs(
    rule_id: int,
    owner_key: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    items = await conditions.list_runs(
        rule_id,
        owner_key=owner_key,
        limit=limit,
    )
    return {"count": len(items), "runs": items}


@app.post("/api/conditions/process")
async def condition_process(
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    core._require_improvement_token(x_jarvis_admin_token)
    count = await conditions.process_once()
    return {"success": True, "processed": count, "status": await conditions.status()}


@app.post("/api/conditions/{rule_id}/pause")
async def condition_pause(
    rule_id: int,
    request: TaskCancelRequest,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    core._require_improvement_token(x_jarvis_admin_token)
    updated = await conditions.pause_rule(
        rule_id,
        owner_key=request.owner_key,
        actor=request.actor,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Active conditional rule not found.")
    return {"success": True, "rule_id": rule_id}


@app.post("/api/conditions/{rule_id}/resume")
async def condition_resume(
    rule_id: int,
    request: TaskCancelRequest,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    core._require_improvement_token(x_jarvis_admin_token)
    updated = await conditions.resume_rule(
        rule_id,
        owner_key=request.owner_key,
        actor=request.actor,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Paused conditional rule not found.")
    return {"success": True, "rule_id": rule_id}


@app.post("/api/conditions/{rule_id}/cancel")
async def condition_cancel(
    rule_id: int,
    request: TaskCancelRequest,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    core._require_improvement_token(x_jarvis_admin_token)
    updated = await conditions.cancel_rule(
        rule_id,
        owner_key=request.owner_key,
        actor=request.actor,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Conditional rule not found.")
    return {"success": True, "rule_id": rule_id}

# Jarvis v16.3.0 multi-step routine API


@app.get("/api/routines/status")
async def routine_status() -> dict[str, Any]:
    return await routines.status()


@app.get("/api/routines")
async def routine_list(
    owner_key: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    statuses = {status} if status else None
    items = await routines.list_routines(
        owner_key=owner_key,
        statuses=statuses,
        limit=limit,
    )
    return {"count": len(items), "routines": items}


@app.get("/api/routines/{routine_id}")
async def routine_get(routine_id: int) -> dict[str, Any]:
    item = await routines.get_routine(routine_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Routine not found.")
    return item


@app.get("/api/routines/{routine_id}/runs")
async def routine_runs(
    routine_id: int,
    owner_key: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    items = await routines.list_runs(
        routine_id,
        owner_key=owner_key,
        limit=limit,
    )
    return {"count": len(items), "runs": items}


@app.post("/api/routines/{routine_id}/run")
async def routine_run(
    routine_id: int,
    request: TaskCancelRequest,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    core._require_improvement_token(x_jarvis_admin_token)
    if not request.owner_key:
        raise HTTPException(status_code=400, detail="owner_key is required.")
    outcome = await routines.run_routine(
        routine_id,
        owner_key=request.owner_key,
        source=request.actor,
    )
    if outcome is None:
        raise HTTPException(status_code=404, detail="Routine not found.")
    return outcome


@app.post("/api/routines/{routine_id}/disable")
async def routine_disable(
    routine_id: int,
    request: TaskCancelRequest,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    core._require_improvement_token(x_jarvis_admin_token)
    if not request.owner_key:
        raise HTTPException(status_code=400, detail="owner_key is required.")
    updated = await routines.disable_routine(routine_id, owner_key=request.owner_key)
    if not updated:
        raise HTTPException(status_code=404, detail="Active routine not found.")
    return {"success": True, "routine_id": routine_id}


@app.post("/api/routines/{routine_id}/enable")
async def routine_enable(
    routine_id: int,
    request: TaskCancelRequest,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    core._require_improvement_token(x_jarvis_admin_token)
    if not request.owner_key:
        raise HTTPException(status_code=400, detail="owner_key is required.")
    updated = await routines.enable_routine(routine_id, owner_key=request.owner_key)
    if not updated:
        raise HTTPException(status_code=404, detail="Disabled routine not found.")
    return {"success": True, "routine_id": routine_id}


@app.post("/api/routines/{routine_id}/delete")
async def routine_delete(
    routine_id: int,
    request: TaskCancelRequest,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    core._require_improvement_token(x_jarvis_admin_token)
    if not request.owner_key:
        raise HTTPException(status_code=400, detail="owner_key is required.")
    updated = await routines.delete_routine(routine_id, owner_key=request.owner_key)
    if not updated:
        raise HTTPException(status_code=404, detail="Routine not found.")
    return {"success": True, "routine_id": routine_id}

# Jarvis v17.0.0 native voice-session observability and Assist update

_VOICE_SESSION_ASSIST_UPDATE_PATH = (
    Path(__file__).resolve().parent
    / "assets"
    / "jarvis-assist-voice-session-v1.6.0.tar.gz"
)


class VoiceSessionCloseRequest(BaseModel):
    reason: str = Field(default="closed", min_length=1, max_length=100)


@app.get(
    "/api/updates/jarvis-assist-voice-session-v1.6.0.tar.gz",
    include_in_schema=False,
)
async def assist_voice_session_update() -> FileResponse:
    if not _VOICE_SESSION_ASSIST_UPDATE_PATH.is_file():
        raise HTTPException(status_code=404, detail="Assist voice-session update not found.")
    return FileResponse(
        _VOICE_SESSION_ASSIST_UPDATE_PATH,
        media_type="application/gzip",
        filename="jarvis-assist-voice-session-v1.6.0.tar.gz",
    )


@app.get("/api/voice-sessions/status")
async def voice_session_status() -> dict[str, Any]:
    return await voice_sessions.status()


@app.get("/api/voice-sessions")
async def voice_session_list(
    active_only: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    items = await voice_sessions.list_sessions(
        active_only=active_only,
        limit=limit,
    )
    return {"count": len(items), "sessions": items}


@app.post("/api/voice-sessions/{session_id}/close")
async def voice_session_close(
    session_id: str,
    request: VoiceSessionCloseRequest,
) -> dict[str, Any]:
    closed = await voice_sessions.close(session_id, request.reason)
    return {"success": True, "closed": closed, "session_id": session_id}

# Jarvis v17.0.2 accepted-turn barge-in observability and Assist update

_BARGE_IN_ASSIST_UPDATE_PATH = (
    Path(__file__).resolve().parent
    / "assets"
    / "jarvis-assist-barge-in-v1.6.2.tar.gz"
)


class VoiceSessionInterruptRequest(BaseModel):
    reason: str = Field(default="accepted_follow_up_during_playback", min_length=1, max_length=100)
    media_player_entity_id: str | None = Field(default=None, max_length=255)


@app.get(
    "/api/updates/jarvis-assist-barge-in-v1.6.2.tar.gz",
    include_in_schema=False,
)
async def assist_barge_in_update() -> FileResponse:
    if not _BARGE_IN_ASSIST_UPDATE_PATH.is_file():
        raise HTTPException(status_code=404, detail="Assist barge-in update not found.")
    return FileResponse(
        _BARGE_IN_ASSIST_UPDATE_PATH,
        media_type="application/gzip",
        filename="jarvis-assist-barge-in-v1.6.2.tar.gz",
    )


@app.post("/api/voice-sessions/{session_id}/interrupt")
async def voice_session_interrupt(
    session_id: str,
    request: VoiceSessionInterruptRequest,
) -> dict[str, Any]:
    record = await voice_sessions.record_interrupt(
        session_id,
        reason=request.reason,
        media_player_entity_id=request.media_player_entity_id,
    )
    return {
        "success": True,
        "recorded": record is not None,
        "session_id": session_id,
        "session": record,
    }

# Jarvis v17.0.3 Companion App voice-session update

_MOBILE_VOICE_ASSIST_UPDATE_PATH = (
    Path(__file__).resolve().parent
    / "assets"
    / "jarvis-assist-mobile-voice-v1.6.3.tar.gz"
)


@app.get(
    "/api/updates/jarvis-assist-mobile-voice-v1.6.3.tar.gz",
    include_in_schema=False,
)
async def assist_mobile_voice_update() -> FileResponse:
    if not _MOBILE_VOICE_ASSIST_UPDATE_PATH.is_file():
        raise HTTPException(status_code=404, detail="Assist mobile voice update not found.")
    return FileResponse(
        _MOBILE_VOICE_ASSIST_UPDATE_PATH,
        media_type="application/gzip",
        filename="jarvis-assist-mobile-voice-v1.6.3.tar.gz",
    )

# Jarvis v17.2.0-r1 low-latency realtime phone voice


async def _realtime_jarvis_tool(
    command: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    request = core.TextCommandRequest(
        text=command,
        conversation_id=str(metadata.get("conversation_id") or "") or None,
        user_id=str(metadata.get("user_id") or "aaron"),
        user_name=str(metadata.get("user_name") or "Aaron"),
        user_is_admin=bool(metadata.get("user_is_admin", True)),
        device_id=str(metadata.get("device_id") or "jarvis_android"),
        voice_mode=True,
    )
    return await core._execute_ai_request(request)


@app.get("/api/realtime/status")
async def realtime_voice_status() -> dict[str, Any]:
    return realtime_voice.status()


@app.websocket("/api/realtime/voice")
async def realtime_voice_socket(websocket: WebSocket) -> None:
    await realtime_voice.handle(websocket, _realtime_jarvis_tool)

