from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import main_v15 as v15
from app.capability_grounding import CapabilityGroundingEngine
from app.task_engine import TemporalActionEngine

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

app = v15.app
app.version = "2.3.7"

_original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _v16_lifespan(application: Any):
    async with _original_lifespan(application):
        await tasks.start()
        try:
            yield
        finally:
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
        "model": "temporal-action-engine-v16.0.7",
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
