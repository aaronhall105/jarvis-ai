import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.logging_config import configure_logging

settings = get_settings()
configure_logging(settings.jarvis_log_level)
logger = logging.getLogger("jarvis-core")


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info(
        "%s starting in %s mode",
        settings.jarvis_name,
        settings.jarvis_environment,
    )
    yield
    logger.info("%s stopping", settings.jarvis_name)


app = FastAPI(
    title=settings.jarvis_name,
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "healthy",
        "service": settings.jarvis_name,
        "version": "0.1.0",
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
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
                width: min(520px, 85vw);
                padding: 32px;
                border: 1px solid #1a6f52;
                border-radius: 18px;
                background: #10171a;
                box-shadow: 0 0 35px rgba(24, 220, 145, 0.12);
            }}
            h1 {{ margin-top: 0; color: #37e69b; }}
            .status {{
                display: inline-block;
                padding: 7px 12px;
                border-radius: 999px;
                background: #123d2e;
                color: #6fffc0;
            }}
            p {{ line-height: 1.6; color: #b9c9c2; }}
        </style>
    </head>
    <body>
        <main class="panel">
            <h1>{settings.jarvis_name}</h1>
            <span class="status">Core online</span>
            <p>Jarvis Core v0.1 is running successfully.</p>
            <p>Realtime voice and Home Assistant connections will be added next.</p>
        </main>
    </body>
    </html>
    """
