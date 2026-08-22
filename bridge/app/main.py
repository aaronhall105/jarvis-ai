import asyncio
import os
import sys
import threading
import traceback
import json
import logging
import re
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.admin_engine import AdminEngine
from app.ai_engine import AIEngine, AIEngineError
from app.code_awareness import CodeAwarenessEngine
from app.config import get_settings
from app.conversation_engine import ConversationEngine
from app.dialogue_manager import DialogueManager
from app.intent_engine import IntentEngine, IntentError
from app.house_awareness import HouseAwarenessEngine
from app.home_assistant import (
    HomeAssistantClient,
    connection_test_with_timeout,
)
from app.logging_config import configure_logging
from app.memory_engine import MemoryEngine
from app.person_room_context import (
    resolve_person_room,
    room_followup_person,
)
from app.self_improvement import SelfImprovementEngine
from app.memory_models import (
    SaveMemoryRequest,
    SearchMemoryRequest,
)
from app.registry import RegistryEngine
from app.realtime_voice import RealtimeVoiceProxy
from app.tool_engine import ToolEngine
from app.tone_engine import ToneEngine
from app.user_context import UserContext
from app.speech_corrections import SpeechCorrectionEngine
from app.runtime_observability import configuration_report, runtime_metrics
from app.system_diagnostics import build_voice_reliability_report
from app.version import CORE_APPLICATION_VERSION, JARVIS_RELEASE

settings = get_settings()
configure_logging(settings.jarvis_log_level)
logger = logging.getLogger("jarvis-core")

home_assistant = HomeAssistantClient(
    base_url=settings.home_assistant_url,
    token=settings.home_assistant_token,
)

registry = RegistryEngine(home_assistant)
tools = ToolEngine(home_assistant, registry)
code_awareness = CodeAwarenessEngine.from_environment()
tone_engine = ToneEngine()
memory = MemoryEngine(
    database_path="/app/data/jarvis_memory.db",
)
conversations = ConversationEngine(
    database_path="/app/data/jarvis_conversations.db",
)
dialogue = DialogueManager(
    database_path="/app/data/jarvis_dialogue.db",
)
improvement = SelfImprovementEngine(
    database_path="/app/data/jarvis_improvement.db",
    enabled=settings.jarvis_self_improvement_enabled,
    auto_prepare=settings.jarvis_self_improvement_auto_prepare,
    repeat_threshold=settings.jarvis_self_improvement_repeat_threshold,
    latency_failure_ms=settings.jarvis_self_improvement_latency_failure_ms,
    core_version=CORE_APPLICATION_VERSION,
)
awareness = HouseAwarenessEngine(
    client=home_assistant,
    registry=registry,
    tools=tools,
    database_path="/app/data/jarvis_house_awareness.db",
    enabled=settings.jarvis_awareness_enabled,
    retention_days=settings.jarvis_awareness_retention_days,
    proactive_enabled=settings.jarvis_proactive_enabled,
    proactive_min_importance=settings.jarvis_proactive_min_importance,
    proactive_target=settings.jarvis_proactive_target,
    proactive_cooldown_seconds=settings.jarvis_proactive_cooldown_seconds,
)
admin = AdminEngine(
    client=home_assistant,
    database_path="/app/data/jarvis_admin.db",
    enabled=settings.jarvis_admin_mode_enabled,
    confirmation_ttl_seconds=settings.jarvis_admin_confirmation_ttl_seconds,
)
intents = IntentEngine(registry, tools)
speech_corrections = SpeechCorrectionEngine(
    "/app/data/jarvis_speech_corrections.db"
)
ai = AIEngine(
    api_key=settings.openai_api_key,
    model=settings.openai_model,
    registry=registry,
    tools=tools,
    memory=memory,
    conversations=conversations,
    admin=admin,
    dialogue=dialogue,
    awareness=awareness,
    code_awareness=code_awareness,
    speech_corrections=speech_corrections,
)


realtime_voice = RealtimeVoiceProxy.from_environment()

_voice_pe_ducked_media: dict[str, dict[str, float]] = {}
_voice_pe_duck_lock = asyncio.Lock()


async def _duck_media_for_voice_pe(metadata: dict[str, object]) -> None:
    session_id = str(metadata.get("session_id") or "")
    area_id = str(metadata.get("area_id") or "")
    if not session_id or not area_id:
        return
    async with _voice_pe_duck_lock:
        entities = await tools.readable_entity_states(refresh=True)
        restored: dict[str, float] = {}
        for entity in entities:
            entity_id = str(entity.get("entity_id") or "")
            if (
                entity.get("domain") != "media_player"
                or entity.get("area_id") != area_id
                or entity.get("state") not in {"playing", "on", "buffering"}
                or "home_assistant_voice" in entity_id
                or entity_id.endswith(".everywhere")
            ):
                continue
            attributes = entity.get("attributes") or {}
            volume = attributes.get("volume_level") if isinstance(attributes, dict) else None
            if not isinstance(volume, (int, float)) or volume <= 0.08:
                continue
            restored[entity_id] = float(volume)
            await home_assistant.call_service(
                "media_player",
                "volume_set",
                entity_ids=[entity_id],
                service_data={"volume_level": max(0.05, float(volume) * 0.35)},
            )
        _voice_pe_ducked_media[session_id] = restored
        logger.info(
            "VOICE_PE_MEDIA_DUCK session=%s area=%s entities=%s",
            session_id,
            area_id,
            sorted(restored),
        )


async def _restore_media_after_voice_pe(metadata: dict[str, object]) -> None:
    session_id = str(metadata.get("session_id") or "")
    async with _voice_pe_duck_lock:
        restored = _voice_pe_ducked_media.pop(session_id, {})
        for entity_id, volume in restored.items():
            await home_assistant.call_service(
                "media_player",
                "volume_set",
                entity_ids=[entity_id],
                service_data={"volume_level": volume},
            )
        logger.info(
            "VOICE_PE_MEDIA_RESTORE session=%s entities=%s",
            session_id,
            sorted(restored),
        )


realtime_voice.voice_pe_session_started = _duck_media_for_voice_pe
realtime_voice.voice_pe_session_ended = _restore_media_after_voice_pe


async def _registry_transcription_prompt(metadata: dict[str, object]) -> str:
    snapshot = await registry.ensure_loaded()
    area_id = str(metadata.get("area_id") or "")
    device_areas = {
        str(device.get("id") or ""): str(device.get("area_id") or "")
        for device in snapshot.devices
    }
    weighted: list[tuple[int, str]] = []

    def add(priority: int, value: object) -> None:
        text = " ".join(str(value or "").replace("_", " ").split()).strip()
        if 2 <= len(text) <= 80:
            weighted.append((priority, text))

    add(100, "Jarvis")
    add(100, "Aaron")
    add(100, "Amber")
    add(100, "TV television")
    user_key = str(metadata.get("user_id") or "guest").strip().casefold() or "guest"
    for learned_term in await speech_corrections.prompt_terms(user_key):
        add(98, learned_term)
    for area in snapshot.areas:
        add(95, area.get("name"))
        for alias in area.get("aliases") or []:
            add(90, alias)
    for device in snapshot.devices:
        priority = 90 if str(device.get("area_id") or "") == area_id else 55
        add(priority, device.get("name_by_user"))
        add(priority - 5, device.get("name"))
    for entity in snapshot.entities:
        if entity.get("disabled_by") is not None:
            continue
        entity_id = str(entity.get("entity_id") or "")
        domain = entity_id.split(".", 1)[0]
        if domain not in {"light", "switch", "media_player", "climate", "script", "person"}:
            continue
        effective_area = str(
            entity.get("area_id")
            or device_areas.get(str(entity.get("device_id") or ""))
            or ""
        )
        priority = 85 if effective_area == area_id else 50
        add(priority, entity.get("name"))
        add(priority - 5, entity.get("original_name"))
        if "." in entity_id:
            add(priority - 10, entity_id.split(".", 1)[1])

    seen: set[str] = set()
    terms: list[str] = []
    base = realtime_voice.config.transcription_prompt.strip()
    prefix = "Household vocabulary: "
    for _, term in sorted(weighted, key=lambda item: (-item[0], item[1].casefold())):
        key = term.casefold()
        if key in seen:
            continue
        candidate_terms = ", ".join([*terms, term])
        candidate_prompt = ". ".join(
            part for part in [base, prefix + candidate_terms] if part
        )
        if len(candidate_prompt) > 1024:
            break
        seen.add(key)
        terms.append(term)
    prompt = ". ".join(
        part for part in [base, prefix + ", ".join(terms)] if part
    )
    logger.info(
        "VOICE_TRANSCRIPTION_VOCAB area=%s user=%s terms=%s characters=%s",
        area_id or "none",
        user_key,
        len(terms),
        len(prompt),
    )
    return prompt


realtime_voice.transcription_prompt_provider = _registry_transcription_prompt


class TextCommandRequest(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=5000,
        examples=["Turn the living room lights off"],
    )
    conversation_id: str | None = None
    user_id: str | None = None
    user_name: str | None = None
    user_is_admin: bool = False
    device_id: str | None = None
    area_id: str | None = None
    satellite_id: str | None = None
    voice_session_id: str | None = None
    voice_session_turn: int | None = None
    voice_endpoint_kind: str | None = None
    voice_mode: bool = False


class ImprovementActionRequest(BaseModel):
    code: str | None = Field(default=None, min_length=6, max_length=12)


class ImprovementCreateRequest(BaseModel):
    request: str = Field(min_length=3, max_length=2000)


def _conversation_scope(
    conversation_id: str | None,
    user_key: str,
) -> tuple[str, str]:
    """Return the external HA ID and isolated local storage ID."""

    external_id = (conversation_id or "").strip() or str(uuid.uuid4())

    # Accept an already-scoped ID from an older in-flight session.
    if external_id.startswith("usr:"):
        return external_id, external_id

    return external_id, f"usr:{user_key}:{external_id}"


# EVENT LOOP STALL DIAGNOSTICS
_EVENT_LOOP_HEARTBEAT_AT = 0.0
_EVENT_LOOP_THREAD_ID: int | None = None
_EVENT_LOOP_MONITOR_STARTED = False


def _event_loop_stack_monitor() -> None:
    """Capture the event-loop stack once per >=300 ms stall."""
    in_stall = False

    while True:
        time.sleep(0.050)

        heartbeat = _EVENT_LOOP_HEARTBEAT_AT
        thread_id = _EVENT_LOOP_THREAD_ID

        if heartbeat <= 0.0 or thread_id is None:
            continue

        stale_ms = (
            time.monotonic() - heartbeat
        ) * 1000.0

        if stale_ms < 300.0:
            in_stall = False
            continue

        if in_stall:
            continue

        in_stall = True

        frame = sys._current_frames().get(thread_id)

        if frame is None:
            continue

        stack = "".join(
            traceback.format_stack(frame)
        )

        timestamp = datetime.now(
            timezone.utc
        ).isoformat()

        message = (
            "\n========== EVENT LOOP STALL STACK =========="
            f"\ntime={timestamp}"
            f"\nstale_ms={stale_ms:.1f}"
            f"\nthread_id={thread_id}"
            "\n"
            + stack
            + "========== EVENT LOOP STALL STACK END ==========\n"
        )

        try:
            os.write(
                2,
                message.encode(
                    "utf-8",
                    errors="replace",
                ),
            )
        except Exception:
            pass


def _start_event_loop_stack_monitor() -> None:
    global _EVENT_LOOP_MONITOR_STARTED

    if _EVENT_LOOP_MONITOR_STARTED:
        return

    _EVENT_LOOP_MONITOR_STARTED = True

    threading.Thread(
        target=_event_loop_stack_monitor,
        name="jarvis-event-loop-stack-monitor",
        daemon=True,
    ).start()


async def _event_loop_watchdog() -> None:
    global _EVENT_LOOP_HEARTBEAT_AT
    global _EVENT_LOOP_THREAD_ID

    _EVENT_LOOP_THREAD_ID = threading.get_ident()
    _EVENT_LOOP_HEARTBEAT_AT = time.monotonic()

    logger.info(
        "Event-loop stall monitor armed threshold_ms=300"
    )

    while True:
        await asyncio.sleep(0.050)
        _EVENT_LOOP_HEARTBEAT_AT = time.monotonic()


@asynccontextmanager
async def lifespan(_: FastAPI):
    _start_event_loop_stack_monitor()

    asyncio.create_task(
        _event_loop_watchdog(),
        name="jarvis_event_loop_watchdog",
    )

    logger.info("%s starting", settings.jarvis_name)

    status = await connection_test_with_timeout(home_assistant)

    if status.connected:
        logger.info("Home Assistant connection successful")

        try:
            await registry.refresh()
        except Exception:
            logger.exception("Initial registry refresh failed")

        admin_status = await admin.check_access()
        if admin_status.get("admin_access"):
            logger.info("Jarvis Admin Mode ready")
        elif admin_status.get("enabled"):
            logger.warning("Jarvis Admin Mode unavailable: %s", admin_status.get("message"))
        else:
            logger.info("Jarvis Admin Mode disabled")

        try:
            await awareness.start()

            proactive_engine.set_state_provider(
                awareness.state_snapshot
            )
            vision_engine.set_state_provider(
                awareness.state_snapshot
            )

            logger.info(
                "Shared Home Assistant state cache wired to "
                "Proactive and Vision Intelligence"
            )
        except Exception:
            logger.exception("House Awareness failed to start")
    else:
        logger.error(
            "Home Assistant connection failed: %s",
            status.message,
        )

    try:
        await proactive_engine.start()
        await vision_engine.start()
        yield
    finally:
        await vision_engine.stop()
        await proactive_engine.stop()
        await awareness.stop()
        logger.info("%s stopping", settings.jarvis_name)


app = FastAPI(
    title=settings.jarvis_name,
    version=CORE_APPLICATION_VERSION,
    lifespan=lifespan,
)


def _liveness_payload() -> dict[str, str]:
    return {
        "status": "healthy",
        "service": settings.jarvis_name,
        "version": CORE_APPLICATION_VERSION,
        "release": JARVIS_RELEASE,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return _liveness_payload()


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return _liveness_payload()


@app.get("/health/ready")
async def health_ready() -> JSONResponse:
    ha_status = await connection_test_with_timeout(home_assistant)
    realtime = realtime_voice.status()
    config = configuration_report(settings, realtime)
    ready = bool(ha_status.connected and config["valid"])
    payload = {
        **_liveness_payload(),
        "status": "ready" if ready else "degraded",
        "ready": ready,
        "home_assistant": {
            "connected": ha_status.connected,
            "message": ha_status.message,
        },
        "configuration": config,
        "realtime_voice": {
            "enabled": realtime.get("enabled"),
            "configured": realtime.get("configured"),
            "active_sessions": realtime.get("active_sessions"),
            "last_error": realtime.get("last_error"),
        },
    }
    return JSONResponse(payload, status_code=200 if ready else 503)


@app.get("/api/system/status")
async def system_status() -> dict[str, object]:
    ha_status = await connection_test_with_timeout(home_assistant)
    realtime = realtime_voice.status()
    try:
        registry_status: dict[str, object] = await registry.summary()
    except Exception as exc:
        logger.exception("Registry status failed")
        runtime_metrics.record_error("registry", str(exc))
        registry_status = {"error": "registry status unavailable"}
    runtime = runtime_metrics.snapshot()
    return {
        "release": JARVIS_RELEASE,
        "core_application_version": CORE_APPLICATION_VERSION,
        "home_assistant": {
            "connected": ha_status.connected,
            "message": ha_status.message,
        },
        "registry": registry_status,
        "realtime_voice": realtime,
        "configuration": configuration_report(settings, realtime),
        "runtime": runtime,
        "voice_reliability": build_voice_reliability_report(
            home_assistant_connected=ha_status.connected,
            realtime=realtime,
            runtime=runtime,
        ),
    }


@app.get("/api/home-assistant/status")
async def home_assistant_status() -> dict[str, object]:
    status = await connection_test_with_timeout(home_assistant)

    return {
        "connected": status.connected,
        "message": status.message,
        "url": settings.home_assistant_url,
    }


@app.get("/api/admin/status")
async def admin_status() -> dict[str, object]:
    status = await admin.check_access()
    return {
        **status,
        "confirmation_ttl_seconds": admin.confirmation_ttl_seconds,
    }


@app.get("/api/admin/audit")
async def admin_audit(limit: int = 50) -> dict[str, object]:
    items = await admin.audit_log(limit=limit)
    return {
        "count": len(items),
        "items": items,
    }


def _privileged_admin_token_valid(
    token: str | None,
) -> bool:
    """Validate the separate credential used for privileged AI authority."""

    expected = (
        settings.jarvis_privileged_admin_token
        .strip()
    )

    supplied = (
        token or ""
    ).strip()

    return bool(
        expected
        and supplied
        and secrets.compare_digest(
            expected,
            supplied,
        )
    )


def _require_improvement_token(token: str | None) -> None:
    expected = settings.jarvis_self_improvement_admin_token.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=(
                "JARVIS_SELF_IMPROVEMENT_ADMIN_TOKEN is not configured. "
                "Use authenticated Home Assistant voice commands or configure the token."
            ),
        )
    supplied = (token or "").strip()
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="Invalid improvement administration token.")


def _require_memory_token(token: str | None) -> None:
    expected = settings.jarvis_memory_admin_token.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="JARVIS_MEMORY_ADMIN_TOKEN is not configured.",
        )
    supplied = (token or "").strip()
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="Invalid memory administration token.")


@app.get("/api/voice/privacy/corrections")
async def voice_privacy_corrections(
    user_id: str = "aaron",
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, object]:
    _require_memory_token(x_jarvis_admin_token)
    user_key = re.sub(r"[^a-z0-9_-]+", "_", user_id.casefold()).strip("_")[:80]
    if not user_key:
        raise HTTPException(status_code=400, detail="Invalid user ID.")
    items = await speech_corrections.list_for_user(user_key)
    return {
        "user_id": user_key,
        "stores_audio": False,
        "count": len(items),
        "items": items,
    }


@app.delete("/api/voice/privacy/corrections")
async def voice_privacy_forget_corrections(
    user_id: str = "aaron",
    heard_as: str | None = None,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, object]:
    _require_memory_token(x_jarvis_admin_token)
    user_key = re.sub(r"[^a-z0-9_-]+", "_", user_id.casefold()).strip("_")[:80]
    if not user_key:
        raise HTTPException(status_code=400, detail="Invalid user ID.")
    deleted = await speech_corrections.forget(user_key, heard_as)
    return {"user_id": user_key, "deleted": deleted, "stores_audio": False}


@app.get("/api/improvement/status")
async def improvement_status() -> dict[str, object]:
    return await improvement.status()


@app.get("/api/improvement/failures")
async def improvement_failures(
    limit: int = 20,
    status: str | None = None,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, object]:
    _require_improvement_token(x_jarvis_admin_token)
    items = await improvement.list_failures(limit=limit, status=status)
    return {"count": len(items), "items": items}


@app.get("/api/improvement/candidates")
async def improvement_candidates(
    limit: int = 20,
    status: str | None = None,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, object]:
    _require_improvement_token(x_jarvis_admin_token)
    items = await improvement.list_candidates(limit=limit, status=status)
    return {"count": len(items), "items": items}


@app.get("/api/improvement/audit")
async def improvement_audit(
    limit: int = 100,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, object]:
    _require_improvement_token(x_jarvis_admin_token)
    items = await improvement.audit_log(limit=limit)
    return {"count": len(items), "items": items}


@app.post("/api/improvement/request")
async def improvement_request(request: ImprovementCreateRequest, x_jarvis_admin_token: str | None = Header(default=None)):
    _require_improvement_token(x_jarvis_admin_token)
    ok, candidate_id, status = await improvement.request_improvement(request.request, actor="api")
    if not ok:
        raise HTTPException(status_code=400, detail=status)
    return {"success": True, "candidate_id": candidate_id, "status": status}


@app.post("/api/improvement/candidates/{candidate_id}/retry")
async def improvement_retry_candidate(
    candidate_id: int,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, object]:
    _require_improvement_token(x_jarvis_admin_token)

    ok, new_candidate_id, state = await improvement.retry_candidate(
        candidate_id,
        actor="api",
    )

    if not ok:
        raise HTTPException(status_code=400, detail=state)

    return {
        "success": True,
        "candidate_id": new_candidate_id,
        "status": state,
        "retry_of_candidate_id": candidate_id,
    }


@app.get("/api/improvement/archive")
async def improvement_archive(
    limit: int = 50,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, object]:
    _require_improvement_token(x_jarvis_admin_token)
    items = await improvement.list_archived_candidates(limit=limit)
    return {"count": len(items), "items": items}


@app.get("/api/improvement/candidates/{candidate_id}")
async def improvement_candidate(
    candidate_id: int,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, object]:
    _require_improvement_token(x_jarvis_admin_token)
    item = await improvement.get_candidate(candidate_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Improvement candidate not found.")
    return item


@app.post("/api/improvement/failures/{failure_id}/prepare")
async def improvement_prepare(
    failure_id: int,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, object]:
    _require_improvement_token(x_jarvis_admin_token)
    ok, candidate_id, state = await improvement.queue_failure(
        failure_id,
        actor="api",
    )
    if not ok:
        raise HTTPException(status_code=400, detail=state)
    return {"success": True, "candidate_id": candidate_id, "status": state}


@app.post("/api/improvement/candidates/{candidate_id}/archive")
async def improvement_archive_candidate(
    candidate_id: int,
    x_jarvis_admin_token: str | None = Header(default=None),
):
    _require_improvement_token(x_jarvis_admin_token)
    ok, state = await improvement.set_candidate_archived(
        candidate_id, True, actor="api"
    )
    if not ok:
        raise HTTPException(status_code=400, detail=state)
    return {"success": True, "status": state}


@app.post("/api/improvement/candidates/{candidate_id}/restore")
async def improvement_restore_candidate(candidate_id: int, x_jarvis_admin_token: str | None = Header(default=None)):
    _require_improvement_token(x_jarvis_admin_token)
    ok, state = await improvement.set_candidate_archived(candidate_id, False, actor="api")
    if not ok:
        raise HTTPException(status_code=400, detail=state)
    return {"success": True, "status": state}


@app.post("/api/improvement/candidates/{candidate_id}/approve")
async def improvement_approve(
    candidate_id: int,
    request: ImprovementActionRequest,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, object]:
    _require_improvement_token(x_jarvis_admin_token)
    result = await improvement.approve_candidate(
        candidate_id,
        request.code or "",
        actor="api",
    )
    return asdict(result)


@app.post("/api/improvement/candidates/{candidate_id}/deploy")
async def improvement_deploy(
    candidate_id: int,
    request: ImprovementActionRequest,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, object]:
    _require_improvement_token(x_jarvis_admin_token)
    result = await improvement.request_deploy(
        candidate_id,
        request.code or "",
        actor="api",
    )
    return asdict(result)


@app.post("/api/improvement/candidates/{candidate_id}/reject")
async def improvement_reject(
    candidate_id: int,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, object]:
    _require_improvement_token(x_jarvis_admin_token)
    result = await improvement.reject_candidate(candidate_id, actor="api")
    return asdict(result)


@app.post("/api/improvement/candidates/{candidate_id}/rollback-ticket")
async def improvement_rollback_ticket(candidate_id: int, x_jarvis_admin_token: str | None = Header(default=None)):
    _require_improvement_token(x_jarvis_admin_token)
    result = await improvement.issue_rollback_ticket(candidate_id, actor="api")
    return asdict(result)


@app.post("/api/improvement/candidates/{candidate_id}/rollback")
async def improvement_rollback(
    candidate_id: int,
    request: ImprovementActionRequest,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, object]:
    _require_improvement_token(x_jarvis_admin_token)
    result = await improvement.request_rollback(
        candidate_id,
        request.code or "",
        actor="api",
    )
    return asdict(result)


@app.get("/api/awareness/status")
async def awareness_status() -> dict[str, object]:
    return await awareness.status()


@app.get("/api/awareness/events")
async def awareness_events(
    minutes: int = 60,
    limit: int = 50,
    area_id: str | None = None,
    min_importance: int = 0,
) -> dict[str, object]:
    events = await awareness.recent_events(
        minutes=minutes,
        limit=limit,
        area_id=area_id,
        min_importance=min_importance,
    )
    return {
        "count": len(events),
        "events": [event.as_dict() for event in events],
    }


@app.get("/api/awareness/summary")
async def awareness_summary(
    minutes: int = 60,
    area_id: str | None = None,
) -> dict[str, object]:
    events = await awareness.recent_events(
        minutes=minutes,
        limit=80,
        area_id=area_id,
    )
    return {
        "count": len(events),
        "summary": awareness.summarise_events(events),
        "events": [event.as_dict() for event in events],
    }


@app.get("/api/awareness/proactive")
async def awareness_proactive(limit: int = 20) -> dict[str, object]:
    events = await awareness.proactive_candidates(limit=limit)
    return {
        "enabled": awareness.proactive_enabled,
        "count": len(events),
        "events": [event.as_dict() for event in events],
    }


@app.post("/api/awareness/proactive/{event_id}/delivered")
async def awareness_mark_delivered(event_id: int) -> dict[str, object]:
    updated = await awareness.mark_proactive_delivered(event_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Awareness event not found.")
    return {"success": True, "event_id": event_id}


@app.get("/api/awareness/active-devices")
async def awareness_active_devices() -> dict[str, object]:
    summary, calls = await awareness.active_devices_summary()
    return {
        "success": True,
        "summary": summary,
        "calls": calls,
    }


@app.get("/api/registry/summary")
async def registry_summary() -> dict[str, object]:
    return await registry.summary()


@app.post("/api/registry/refresh")
async def refresh_registry() -> dict[str, object]:
    await registry.refresh()

    return {
        "success": True,
        **await registry.summary(),
    }


@app.get("/api/registry/areas")
async def registry_areas() -> dict[str, object]:
    areas = await registry.areas()

    return {
        "count": len(areas),
        "areas": areas,
    }


@app.get("/api/registry/areas/{area_id}")
async def registry_area(area_id: str) -> dict[str, object]:
    room = await registry.room(area_id)

    if room is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown Home Assistant area: {area_id}",
        )

    return room


@app.get("/api/tools/lights/{area_id}")
async def area_lights(area_id: str) -> dict[str, object]:
    try:
        lights = await tools.lights_in_area(area_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    return {
        "area_id": area_id,
        "count": len(lights),
        "lights": lights,
    }


@app.post("/api/tools/lights/{area_id}/on")
async def turn_area_lights_on(
    area_id: str,
) -> dict[str, object]:
    try:
        return await tools.control_area_lights(
            area_id=area_id,
            turn_on=True,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc


@app.post("/api/tools/lights/{area_id}/off")
async def turn_area_lights_off(
    area_id: str,
) -> dict[str, object]:
    try:
        return await tools.control_area_lights(
            area_id=area_id,
            turn_on=False,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc



@app.get("/api/tools/devices")
async def controllable_devices() -> dict[str, object]:
    devices = await tools.controllable_devices()

    return {
        "count": len(devices),
        "devices": devices,
    }


@app.get("/api/tools/devices/search")
async def search_devices(
    q: str,
    limit: int = 20,
) -> dict[str, object]:
    devices = await tools.search_devices(
        query=q,
        limit=limit,
    )

    return {
        "query": q,
        "count": len(devices),
        "devices": devices,
    }


@app.post("/api/tools/devices/{entity_id:path}/on")
async def turn_device_on(
    entity_id: str,
) -> dict[str, object]:
    try:
        return await tools.control_device(
            entity_id=entity_id,
            turn_on=True,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc


@app.post("/api/tools/devices/{entity_id:path}/off")
async def turn_device_off(
    entity_id: str,
) -> dict[str, object]:
    try:
        return await tools.control_device(
            entity_id=entity_id,
            turn_on=False,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc


@app.post("/api/assistant/text")
async def assistant_text(
    request: TextCommandRequest,
) -> dict[str, object]:
    try:
        return await intents.execute(request.text)
    except IntentError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc



async def _execute_ai_request(
    request: TextCommandRequest,
    on_text_delta: Callable[[str], Awaitable[None]] | None = None,
    trusted_context: dict[str, object] | None = None,
    privilege_verified: bool = False,
) -> dict[str, object]:
    """Execute one user-scoped Jarvis request."""

    request_started = time.monotonic()
    vision_payload: dict[str, object] | None = None
    actor = UserContext.from_request(
        user_id=request.user_id,
        user_name=request.user_name,
        user_is_admin=request.user_is_admin,
        device_id=request.device_id,
        voice_mode=request.voice_mode,
        privilege_verified=privilege_verified,
        area_id=request.area_id,
    )
    external_conversation_id, storage_conversation_id = _conversation_scope(
        request.conversation_id,
        actor.user_key,
    )

    conversation = await conversations.ensure_conversation(
        conversation_id=storage_conversation_id,
        source=f"home_assistant:{actor.user_key}",
    )
    storage_conversation_id = str(conversation["conversation_id"])

    proactive_reply = await proactive_engine.handle_reply(
        request.text, actor.user_key
    )
    if proactive_reply is not None:
        response = str(proactive_reply["response"])
        await conversations.add_user_message(
            conversation_id=storage_conversation_id, content=request.text
        )
        await conversations.add_assistant_message(
            conversation_id=storage_conversation_id, content=response
        )
        return {
            "success": True,
            "response": response,
            "model": "proactive-initiative",
            "intent": "proactive_reply",
            "deterministic": True,
            "tool_called": False,
            "tool_rounds": 0,
            "calls": [],
            "memory_used": False,
            "conversation_id": storage_conversation_id,
            "proactive_event": proactive_reply["event"],
            "usage": {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0},
        }

    history_before = await conversations.get_ai_history(
        conversation_id=storage_conversation_id,
        limit=12,
    )
    room_person = room_followup_person(
        request.text,
        history_before,
    )

    room_result: dict[str, object] | None = None

    if room_person:
        try:
            fresh_states = await tools.readable_entity_states(
                refresh=True
            )
            room_evidence = (
                await vision_engine.person_room_evidence()
            )
            room_result = resolve_person_room(
                room_person,
                fresh_states,
                room_evidence,
            )

            primary = room_result.get(
                "primary_event"
            )
            evidence_events = room_evidence.get(
                "events"
            )
            if (
                isinstance(primary, dict)
                or isinstance(evidence_events, list)
            ):
                vision_payload = {
                    "primary_event": primary,
                    "events": (
                        evidence_events
                        if isinstance(
                            evidence_events,
                            list,
                        )
                        else []
                    ),
                }
        except Exception:
            logger.exception(
                "Person room context lookup failed"
            )
            room_result = {
                "handled": True,
                "response": (
                    f"I can confirm {room_person.title()}'s "
                    "home status, but the cameras could not "
                    "provide a reliable room match."
                ),
            }

    if (
        room_result is not None
        and bool(room_result.get("handled"))
    ):
        response = str(
            room_result.get("response") or ""
        ).strip()
        await conversations.add_user_message(
            conversation_id=storage_conversation_id,
            content=request.text,
        )
        await conversations.add_assistant_message(
            conversation_id=storage_conversation_id,
            content=response,
        )
        result = {
            "success": True,
            "response": response,
            "model": "person-room-context",
            "intent": "person_room_follow_up",
            "deterministic": True,
            "tool_called": True,
            "tool_rounds": 1,
            "calls": [{
                "tool": "person_room_context",
                "success": True,
                "message": response,
            }],
            "memory_used": False,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
            },
        }
    else:
        improvement_command = (
            await improvement.handle_command(
                text=request.text,
                actor=actor,
                conversation_id=storage_conversation_id,
            )
        )

        if improvement_command.handled:
            await conversations.add_user_message(
                conversation_id=storage_conversation_id,
                content=request.text,
            )
            await conversations.add_assistant_message(
                conversation_id=storage_conversation_id,
                content=improvement_command.response,
            )
            result = {
                "success": improvement_command.success,
                "response": improvement_command.response,
                "model": "self-improvement",
                "intent": improvement_command.intent,
                "deterministic": True,
                "tool_called": False,
                "tool_rounds": 0,
                "calls": [],
                "memory_used": False,
                "conversation_id": storage_conversation_id,
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached_tokens": 0,
                },
            }
            if improvement_command.details is not None:
                result["improvement"] = (
                    improvement_command.details
                )
        else:
            await improvement.capture_feedback_before_request(
                conversation_id=storage_conversation_id,
                actor=actor,
                raw_text=request.text,
            )
            if vision_engine.matches_query(request.text):
                try:
                    vision_payload = (
                        await vision_engine.context_for_query(
                            request.text
                        )
                    )
                except Exception:
                    logger.exception(
                        "Vision Intelligence context lookup failed"
                    )
                else:
                    prompt = vision_payload.get("prompt")
                    if (
                        isinstance(prompt, str)
                        and prompt.strip()
                    ):
                        trusted_context = dict(
                            trusted_context or {}
                        )
                        trusted_context[
                            "vision_context"
                        ] = prompt

            result = await ai.ask(
                text=request.text,
                conversation_id=storage_conversation_id,
                actor=actor,
                on_text_delta=on_text_delta,
                trusted_context=trusted_context,
            )

    if vision_payload:
        primary = vision_payload.get("primary_event")
        if isinstance(primary, dict):
            result["vision_event"] = primary
        related = vision_payload.get("events")
        if isinstance(related, list):
            result["vision_events"] = related[:8]

    # Home Assistant should keep its own opaque conversation ID. Jarvis uses a
    # user-scoped ID internally so Aaron and Amber can never share history.
    result["conversation_id"] = external_conversation_id
    result["message_count"] = await conversations.message_count(
        storage_conversation_id
    )
    result["user"] = {
        "key": actor.user_key,
        "name": actor.display_name,
        "is_admin": actor.is_admin,
        "privilege_verified": (
            actor.privilege_verified
        ),
    }
    timings = result.get("timings")
    if not isinstance(timings, dict):
        timings = {}
    timings["jarvis_request_total_ms"] = round(
        (time.monotonic() - request_started) * 1000
    )
    result["timings"] = timings
    runtime_metrics.increment("assistant_turns")
    if request.voice_mode:
        if bool(result.get("deterministic")):
            runtime_metrics.increment("voice_local_fast_path_turns")
        else:
            runtime_metrics.increment("voice_model_reasoning_turns")
    runtime_metrics.observe_many(timings)

    try:
        await improvement.observe_interaction(
            conversation_id=storage_conversation_id,
            actor=actor,
            raw_text=request.text,
            result=result,
        )
    except Exception:
        logger.exception("Could not record self-improvement interaction evidence")

    return result



async def _realtime_brain_handler(
    text: str,
    metadata: dict[str, object],
    on_text_delta: Callable[[str], Awaitable[None]],
) -> dict[str, object]:
    """Send one mobile voice turn through the normal Jarvis brain."""

    conversation_id = str(
        metadata.get("conversation_id") or ""
    ).strip() or None

    user_id = str(
        metadata.get("user_id") or ""
    ).strip() or None

    user_name = str(
        metadata.get("user_name") or ""
    ).strip() or None

    device_id = str(
        metadata.get("device_id") or ""
    ).strip() or None

    area_id = str(
        metadata.get("area_id") or ""
    ).strip() or None

    endpoint_kind = str(
        metadata.get("voice_endpoint_kind") or "android"
    ).strip() or "android"

    session_id = str(
        metadata.get("session_id") or ""
    ).strip() or None

    request = TextCommandRequest(
        text=text,
        conversation_id=conversation_id,
        user_id=user_id,
        user_name=user_name,
        user_is_admin=bool(
            metadata.get("user_is_admin", False)
        ),
        device_id=device_id,
        area_id=area_id,
        satellite_id=(
            device_id
            if endpoint_kind == "voice_pe"
            else None
        ),
        voice_session_id=session_id,
        voice_endpoint_kind=(
            "voice_pe_realtime"
            if endpoint_kind == "voice_pe"
            else "android_realtime"
        ),
        voice_mode=True,
    )

    trusted_context = {
        "timezone": metadata.get("timezone"),
        "local_datetime": metadata.get("local_datetime"),
        "local_date": metadata.get("local_date"),
        "local_time": metadata.get("local_time"),
        "utc_offset_seconds": metadata.get("utc_offset_seconds"),
        "transcription_confidence": metadata.get("transcription_confidence"),
    }

    # This handler is reachable only after RealtimeVoiceProxy
    # successfully validates the mobile or Voice PE token. Admin
    # privilege is therefore trusted only when the authenticated
    # realtime identity was also resolved as Aaron/admin.
    realtime_privilege_verified = bool(
        metadata.get(
            "user_is_admin",
            False,
        )
    )

    return await _execute_ai_request(
        request,
        on_text_delta=on_text_delta,
        trusted_context=trusted_context,
        privilege_verified=(
            realtime_privilege_verified
        ),
    )


@app.get("/api/realtime/voice/status")
async def realtime_voice_status() -> dict[str, object]:
    """Report Android realtime voice availability."""

    return realtime_voice.status()


@app.websocket("/api/realtime/voice")
async def realtime_voice_websocket(
    websocket: WebSocket,
) -> None:
    """Handle Android realtime voice connections."""

    await realtime_voice.handle(
        websocket,
        _realtime_brain_handler,
    )




@app.post("/api/assistant/ai")
async def assistant_ai(
    request: TextCommandRequest,
    x_jarvis_admin_token: str | None = Header(
        default=None
    ),
) -> dict[str, object]:
    try:
        return await _execute_ai_request(
            request,
            privilege_verified=(
                _privileged_admin_token_valid(
                    x_jarvis_admin_token
                )
            ),
        )
    except AIEngineError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc


@app.post("/api/assistant/ai/stream")
async def assistant_ai_stream(
    request: TextCommandRequest,
    x_jarvis_admin_token: str | None = Header(
        default=None
    ),
) -> StreamingResponse:
    """Stream Jarvis text deltas as newline-delimited JSON."""

    privilege_verified = (
        _privileged_admin_token_valid(
            x_jarvis_admin_token
        )
    )

    async def stream_events() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue(
            maxsize=256
        )

        stream_started = time.monotonic()
        first_answer_event = asyncio.Event()
        first_output_ms: int | None = None

        async def on_text_delta(delta: str) -> None:
            nonlocal first_output_ms
            if first_output_ms is None:
                first_output_ms = round((time.monotonic() - stream_started) * 1000)
            first_answer_event.set()
            await queue.put({"type": "delta", "delta": delta})

        async def delayed_progress() -> None:
            nonlocal first_output_ms
            # Spoken progress is only useful for a real voice pipeline. Typed Assist
            # remains clean, and quick deterministic replies complete without filler.
            if not request.voice_mode:
                return
            profile = tone_engine.analyse(request.text)
            if not tone_engine.should_emit_progress(request.text, profile):
                return
            await asyncio.sleep(
                tone_engine.progress_delay_seconds(request.text, profile)
            )
            if first_answer_event.is_set():
                return
            phrase = tone_engine.progress_phrase(request.text, profile).strip()
            if not phrase:
                return
            if first_output_ms is None:
                first_output_ms = round((time.monotonic() - stream_started) * 1000)
            await queue.put({
                "type": "progress",
                "message": phrase,
                "presentation": "spoken_thinking",
            })

        async def run_request() -> None:
            try:
                result = await _execute_ai_request(
                    request,
                    on_text_delta=on_text_delta,
                    privilege_verified=(
                        privilege_verified
                    ),
                )
                first_answer_event.set()
                timings = result.get("timings")
                if not isinstance(timings, dict):
                    timings = {}
                timings["stream_first_output_ms"] = first_output_ms
                timings["stream_total_ms"] = round(
                    (time.monotonic() - stream_started) * 1000
                )
                result["timings"] = timings
                await queue.put({"type": "final", "result": result})
            except AIEngineError as exc:
                first_answer_event.set()
                await queue.put({
                    "type": "error",
                    "message": str(exc),
                })
            except Exception:
                first_answer_event.set()
                logger.exception("Unexpected Jarvis streaming failure")
                await queue.put({
                    "type": "error",
                    "message": "Jarvis encountered an unexpected streaming error.",
                })
            finally:
                await queue.put(None)

        task = asyncio.create_task(
            run_request(),
            name="jarvis_streaming_response",
        )
        progress_task = asyncio.create_task(
            delayed_progress(),
            name="jarvis_streaming_progress",
        )

        yield json.dumps(
            {"type": "start", "version": CORE_APPLICATION_VERSION, "release": JARVIS_RELEASE},
            separators=(",", ":"),
        ) + "\n"

        try:
            while True:
                try:
                    async with asyncio.timeout(5):
                        event = await queue.get()
                except TimeoutError:
                    # Keep local proxies and Home Assistant's HTTP client from
                    # treating a quiet model/tool phase as a dead connection.
                    yield '{"type":"ping"}\n'
                    continue

                if event is None:
                    break
                yield json.dumps(
                    event,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ) + "\n"
        finally:
            if not progress_task.done():
                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    pass
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    return StreamingResponse(
        stream_events(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/conversations")
async def list_conversations(
    limit: int = 50,
) -> dict[str, object]:
    items = await conversations.list_conversations(limit=limit)
    return {
        "count": len(items),
        "conversations": items,
    }


@app.get("/api/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    limit: int = 100,
) -> dict[str, object]:
    conversation = await conversations.get_conversation(
        conversation_id
    )
    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found.",
        )

    messages = await conversations.get_messages(
        conversation_id=conversation_id,
        limit=limit,
    )
    return {
        "conversation": conversation,
        "messages": messages,
        "message_count": len(messages),
    }


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
) -> dict[str, object]:
    deleted = await conversations.delete_conversation(
        conversation_id
    )
    await dialogue.delete(conversation_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found.",
        )

    return {
        "success": True,
        "conversation_id": conversation_id,
    }




@app.get("/api/memory")
async def list_memories(
    limit: int = 100,
    requester_key: str = "aaron",
    x_jarvis_memory_token: str | None = Header(default=None),
) -> dict[str, object]:
    _require_memory_token(x_jarvis_memory_token)
    memories = await memory.list_memories(
        limit=limit,
        owner_key=requester_key,
    )
    return {"count": len(memories), "memories": memories}


@app.post("/api/memory")
async def save_memory(
    request: SaveMemoryRequest,
    x_jarvis_memory_token: str | None = Header(default=None),
) -> dict[str, object]:
    _require_memory_token(x_jarvis_memory_token)
    saved = await memory.save(
        category=request.category,
        subject=request.subject,
        content=request.content,
        owner_key=request.owner_key,
        subject_key=request.subject_key,
        visibility=request.visibility,
        sensitivity=request.sensitivity,
        source=request.source,
        confidence=request.confidence,
        expires_at=request.expires_at,
    )
    return {"success": True, "memory": saved}


@app.post("/api/memory/search")
async def search_memories(
    request: SearchMemoryRequest,
    x_jarvis_memory_token: str | None = Header(default=None),
) -> dict[str, object]:
    _require_memory_token(x_jarvis_memory_token)
    memories = await memory.search(
        query=request.query,
        limit=request.limit,
        owner_key=request.requester_key,
    )
    return {"count": len(memories), "memories": memories}


@app.delete("/api/memory/{memory_id}")
async def delete_memory(
    memory_id: int,
    requester_key: str = "aaron",
    x_jarvis_memory_token: str | None = Header(default=None),
) -> dict[str, object]:
    _require_memory_token(x_jarvis_memory_token)
    deleted = await memory.delete_by_id(
        memory_id,
        owner_key=requester_key,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found or not editable.")
    return {"success": True, "deleted_id": memory_id}


@app.get("/api/memory/{memory_id}/history")
async def memory_history(
    memory_id: int,
    requester_key: str = "aaron",
    x_jarvis_memory_token: str | None = Header(default=None),
) -> dict[str, object]:
    _require_memory_token(x_jarvis_memory_token)
    revisions = await memory.history(memory_id, owner_key=requester_key)
    return {"memory_id": memory_id, "count": len(revisions), "revisions": revisions}


@app.post("/api/memory/{memory_id}/restore")
async def restore_memory(
    memory_id: int,
    requester_key: str = "aaron",
    x_jarvis_memory_token: str | None = Header(default=None),
) -> dict[str, object]:
    _require_memory_token(x_jarvis_memory_token)
    restored = await memory.restore(memory_id, owner_key=requester_key)
    if restored is None:
        raise HTTPException(
            status_code=404, detail="Retired memory not found or not editable."
        )
    return {"success": True, "memory": restored}


@app.get("/api/memory/status")
async def memory_status(
    requester_key: str = "aaron",
    x_jarvis_memory_token: str | None = Header(default=None),
) -> dict[str, object]:
    _require_memory_token(x_jarvis_memory_token)
    return await memory.status(owner_key=requester_key)


@app.get("/chat", response_class=FileResponse)
async def chat_page() -> FileResponse:
    return FileResponse(
        "app/static/chat.html",
        media_type="text/html",
    )

@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    status = await connection_test_with_timeout(home_assistant)

    try:
        summary = await registry.summary()
        registry_text = (
            f'{summary["areas"]} areas · '
            f'{summary["devices"]} devices · '
            f'{summary["entities"]} entities'
        )
    except Exception:
        logger.exception("Unable to load dashboard registry summary")
        registry_text = "Registry unavailable"

    ha_class = "online" if status.connected else "offline"
    ha_text = (
        "Home Assistant connected"
        if status.connected
        else "Home Assistant disconnected"
    )

    return f"""
    <!doctype html>
    <html lang="en">
    <head>
        <meta charset="utf-8">
        <meta name="viewport"
              content="width=device-width,initial-scale=1">
        <title>{settings.jarvis_name}</title>
        <style>
            body {{
                margin: 0;
                background: #080b0f;
                color: #e9fff5;
                font-family: Arial, sans-serif;
                display: grid;
                place-items: center;
                min-height: 100vh;
            }}
            main {{
                box-sizing: border-box;
                width: min(600px, 90vw);
                padding: 32px;
                background: #10171a;
                border: 1px solid #1a6f52;
                border-radius: 18px;
            }}
            h1 {{ color: #37e69b; }}
            .status {{
                display: inline-block;
                margin: 4px;
                padding: 7px 12px;
                border-radius: 999px;
            }}
            .online {{
                background: #123d2e;
                color: #6fffc0;
            }}
            .offline {{
                background: #4a2020;
                color: #ff9d9d;
            }}
            .registry {{
                margin: 24px 0;
                padding: 18px;
                border-radius: 12px;
                background: #0a1113;
                color: #b9c9c2;
            }}
            a {{
                color: #6fffc0;
                margin-right: 14px;
            }}
        </style>
    </head>
    <body>
        <main>
            <h1>{settings.jarvis_name}</h1>
            <span class="status online">Core online</span>
            <span class="status {ha_class}">{ha_text}</span>

            <div class="registry">
                <strong>Home model</strong>
                <p>{registry_text}</p>
            </div>

            <a href="/api/registry/areas">View rooms</a>
            <a href="/api/registry/summary">View summary</a>
            <a href="/docs">API panel</a>
        </main>
    </body>
    </html>
    """

# Jarvis v19 alpha8 proactive router
from app.proactive_intelligence import (
    engine as proactive_engine,
    router as proactive_router,
)
app.include_router(proactive_router)

# Jarvis v19 alpha9 Core-first vision intelligence
from app.vision_intelligence import engine as vision_engine
from app.vision_intelligence import router as vision_router
app.include_router(vision_router)
