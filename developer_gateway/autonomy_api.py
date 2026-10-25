"""Opt-in observe-only HTTP surface; no change to manual Developer sessions."""

import asyncio
from typing import Any, Callable

from fastapi import APIRouter, Header, HTTPException, Request

from .autonomy import Governor
from .autonomy_host import collect_report, guardian_status, relay_operator


def router(governor: Callable[[], Governor | None], authorised: Callable) -> APIRouter:
    routes = APIRouter(prefix="/api/autonomy")

    def require(authorization: str | None) -> Governor:
        if not authorised(authorization):
            raise HTTPException(401, "Authentication required")
        current = governor()
        if current is None:
            raise HTTPException(503, "Observe-only service is not configured")
        return current

    @routes.get("")
    async def status(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        current = require(authorization)
        guardian = await asyncio.to_thread(guardian_status)
        return {
            **current.snapshot(guardian),
            "guardian_generation": guardian.get("generation"),
            "operator_control": "signed_operator_required",
        }

    @routes.post("/observe")
    async def observe(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        current = require(authorization)
        await current.refresh(collect_report)
        return await status(authorization)

    @routes.post("/control")
    async def control(request: Request, authorization: str | None = Header(default=None)):
        require(authorization)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 8192:
                raise HTTPException(413, "Request too large")
        import json

        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError()
        except (ValueError, RecursionError):
            raise HTTPException(400, "Invalid control request") from None
        if body.get("action") == "resume":
            # Phase 1 is a STOPPED observation surface, not an activation route.
            # Even an enrolled operator must use the separately authorized host
            # ceremony for later phases. Never forward RESUME to the broker here.
            raise HTTPException(403, "Activation is unavailable in observe-only Phase 1")
        result = await asyncio.to_thread(relay_operator, body)
        if not result["ok"]:
            raise HTTPException(403, "Signed operator authorization required or unavailable")
        return result

    return routes
