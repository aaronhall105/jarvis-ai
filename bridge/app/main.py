import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.home_assistant import (
    HomeAssistantClient,
    connection_test_with_timeout,
)
from app.logging_config import configure_logging

settings = get_settings()
configure_logging(settings.jarvis_log_level)
logger = logging.getLogger("jarvis-core")

home_assistant = HomeAssistantClient(
    base_url=settings.home_assistant_url,
    token=settings.home_assistant_token,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info(
        "%s starting in %s mode",
        settings.jarvis_name,
        settings.jarvis_environment,
    )

    status = await connection_test_with_timeout(home_assistant)

    if status.connected:
        logger.info("Home Assistant connection successful")
    else:
        logger.warning("Home Assistant connection failed: %s", status.message)

    yield

    logger.info("%s stopping", settings.jarvis_name)


app = FastAPI(
    title=settings.jarvis_name,
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "healthy",
        "service": settings.jarvis_name,
        "version": "0.2.0",
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/home-assistant/status")
async def home_assistant_status() -> dict[str, str | bool]:
    status = await connection_test_with_timeout(home_assistant)

    return {
        "connected": status.connected,
        "message": status.message,
        "url": settings.home_assistant_url,
    }


@app.get("/api/home-assistant/entities")
async def home_assistant_entities() -> dict[str, object]:
    try:
        states = await home_assistant.get_states()
    except Exception as exc:
        logger.exception("Unable to retrieve Home Assistant entities")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "count": len(states),
        "entities": states,
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    status = await connection_test_with_timeout(home_assistant)

    if status.connected:
        connection_text = "Home Assistant connected"
        connection_class = "online"
    else:
        connection_text = "Home Assistant disconnected"
        connection_class = "offline"

    return f"""
    <!doctype html>
    <html lang="en">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
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
            .panel {{
                box-sizing: border-box;
                width: min(520px, 90vw);
                padding: 32px;
                border: 1px solid #1a6f52;
                border-radius: 18px;
                background: #10171a;
                box-shadow: 0 0 35px rgba(24, 220, 145, 0.12);
            }}
            h1 {{
                margin-top: 0;
                color: #37e69b;
            }}
            .status {{
                display: inline-block;
                margin: 5px 6px 5px 0;
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
            p {{
                line-height: 1.6;
                color: #b9c9c2;
            }}
            a {{
                color: #6fffc0;
            }}
        </style>
    </head>
    <body>
        <main class="panel">
            <h1>{settings.jarvis_name}</h1>
            <span class="status online">Core online</span>
            <span class="status {connection_class}">{connection_text}</span>
            <p>Jarvis Core v0.2 is running.</p>
            <p>
                <a href="/api/home-assistant/status">
                    View Home Assistant connection status
                </a>
            </p>
        </main>
    </body>
    </html>
    """
