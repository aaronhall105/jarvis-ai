"""Opt-in observe-only HTTP surface; no change to manual Developer sessions."""

import asyncio
from typing import Any, Callable

from fastapi import APIRouter, Header, HTTPException, Request

from .autonomy import Governor
from .autonomy_host import collect_report, development_lifecycle, guardian_status, relay_operator
from . import development_client


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
        development = await asyncio.to_thread(development_lifecycle)
        return {
            **current.snapshot(guardian),
            "guardian_generation": guardian.get("generation"),
            "operator_control": "signed_operator_required",
            "development_task": development.get("development_task"),
            "development_status": {
                key: value for key, value in development.items() if key != "development_task"
            },
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
            category = result.get("category")
            if category == "approval_expired":
                raise HTTPException(410, "Development approval expired; refresh the task")
            if category == "operator_unavailable":
                raise HTTPException(503, "Protected operator service unavailable")
            raise HTTPException(403, "Signed operator authorization was rejected")
        return result

    async def product_preview(view: str, identifier: int | str, authorization: str | None):
        require(authorization)
        try:
            result = await asyncio.to_thread(development_client.preview, view, identifier)
        except ValueError:
            raise HTTPException(400, "Invalid development identifier") from None
        # Do not disguise an unavailable protected publisher as an empty form.
        # A blocked, valid read is useful explanatory state, not an approval.
        if result["status"] == "unavailable":
            raise HTTPException(503, "Protected development publication unavailable")
        return result

    @routes.get("/development/prepare/{candidate_id}")
    async def preparation(candidate_id: int, authorization: str | None = Header(default=None)):
        return await product_preview("prepare", candidate_id, authorization)

    @routes.get("/development/activation/{task_id}")
    async def activation(task_id: str, authorization: str | None = Header(default=None)):
        return await product_preview("activation", task_id, authorization)

    @routes.get("/development/proposal/{task_id}")
    async def proposal(task_id: str, authorization: str | None = Header(default=None)):
        return await product_preview("proposal", task_id, authorization)

    async def signed_product_action(action: str, request: Request, authorization: str | None):
        require(authorization)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > development_client.MAX_REQUEST:
                raise HTTPException(413, "Request too large")
        try:
            body = development_client.decode(raw)
        except (ValueError, RecursionError):
            raise HTTPException(400, "Invalid signed request") from None
        result = await asyncio.to_thread(development_client.relay, action, body)
        if not result["ok"]:
            category = result["category"]
            if category in {"approval_expired", "request_expired"}:
                raise HTTPException(410, "Approval expired; refresh before signing")
            if category in {"outcome_unconfirmed", "integration_reconciliation_required"}:
                raise HTTPException(503, "Outcome unconfirmed; refresh status, do not resend")
            if category == "integration_authority_unprovisioned":
                raise HTTPException(503, "Protected PR integration is not provisioned")
            raise HTTPException(403, "Signed authorization rejected; no success confirmed")
        return result

    @routes.post("/development/start")
    async def start(request: Request, authorization: str | None = Header(default=None)):
        return await signed_product_action("prepare_development", request, authorization)

    @routes.post("/development/activate")
    async def activate(request: Request, authorization: str | None = Header(default=None)):
        # This is a distinct, task-bound, signed capability, not HTTP RESUME.
        # The broker checks the prior genuine task signature and generation.
        return await signed_product_action("activate_development", request, authorization)

    @routes.post("/development/publish")
    async def publish(request: Request, authorization: str | None = Header(default=None)):
        return await signed_product_action("create_development_pr", request, authorization)

    return routes
