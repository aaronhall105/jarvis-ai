import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from app.ai_engine import AIEngine, AIEngineError
from app.config import get_settings
from app.conversation_engine import ConversationEngine
from app.intent_engine import IntentEngine, IntentError
from app.home_assistant import (
    HomeAssistantClient,
    connection_test_with_timeout,
)
from app.logging_config import configure_logging
from app.memory_engine import MemoryEngine
from app.memory_models import (
    SaveMemoryRequest,
    SearchMemoryRequest,
)
from app.registry import RegistryEngine
from app.tool_engine import ToolEngine

settings = get_settings()
configure_logging(settings.jarvis_log_level)
logger = logging.getLogger("jarvis-core")

home_assistant = HomeAssistantClient(
    base_url=settings.home_assistant_url,
    token=settings.home_assistant_token,
)

registry = RegistryEngine(home_assistant)
tools = ToolEngine(home_assistant, registry)
memory = MemoryEngine(
    database_path="/app/data/jarvis_memory.db",
)
conversations = ConversationEngine(
    database_path="/app/data/jarvis_conversations.db",
)
intents = IntentEngine(registry, tools)
ai = AIEngine(
    api_key=settings.openai_api_key,
    model=settings.openai_model,
    registry=registry,
    tools=tools,
    memory=memory,
    conversations=conversations,
)


class TextCommandRequest(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=500,
        examples=["Turn the living room lights off"],
    )
    conversation_id: str | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("%s starting", settings.jarvis_name)

    status = await connection_test_with_timeout(home_assistant)

    if status.connected:
        logger.info("Home Assistant connection successful")

        try:
            await registry.refresh()
        except Exception:
            logger.exception("Initial registry refresh failed")
    else:
        logger.error(
            "Home Assistant connection failed: %s",
            status.message,
        )

    yield
    logger.info("%s stopping", settings.jarvis_name)


app = FastAPI(
    title=settings.jarvis_name,
    version="1.3.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "healthy",
        "service": settings.jarvis_name,
        "version": "1.2.0",
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/home-assistant/status")
async def home_assistant_status() -> dict[str, object]:
    status = await connection_test_with_timeout(home_assistant)

    return {
        "connected": status.connected,
        "message": status.message,
        "url": settings.home_assistant_url,
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



@app.post("/api/assistant/ai")
async def assistant_ai(
    request: TextCommandRequest,
) -> dict[str, object]:
    conversation = await conversations.ensure_conversation(
        conversation_id=request.conversation_id,
        source="api",
    )
    conversation_id = conversation["conversation_id"]

    try:
        result = await ai.ask(
            text=request.text,
            conversation_id=conversation_id,
        )
    except AIEngineError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    result["conversation_id"] = conversation_id
    result["message_count"] = await conversations.message_count(
        conversation_id
    )
    return result


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
) -> dict[str, object]:
    memories = await memory.list_memories(
        limit=limit,
    )
    return {
        "count": len(memories),
        "memories": memories,
    }


@app.post("/api/memory")
async def save_memory(
    request: SaveMemoryRequest,
) -> dict[str, object]:
    saved = await memory.save(
        category=request.category,
        subject=request.subject,
        content=request.content,
    )
    return {
        "success": True,
        "memory": saved,
    }


@app.post("/api/memory/search")
async def search_memories(
    request: SearchMemoryRequest,
) -> dict[str, object]:
    memories = await memory.search(
        query=request.query,
        limit=request.limit,
    )
    return {
        "count": len(memories),
        "memories": memories,
    }


@app.delete("/api/memory/{memory_id}")
async def delete_memory(
    memory_id: int,
) -> dict[str, object]:
    deleted = await memory.delete_by_id(
        memory_id,
    )

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Memory not found.",
        )

    return {
        "success": True,
        "deleted_id": memory_id,
    }


@app.get("/api/memory/status")
async def memory_status() -> dict[str, object]:
    return {
        "status": "ready",
        "count": await memory.count(),
        "database": "jarvis_memory.db",
    }


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


