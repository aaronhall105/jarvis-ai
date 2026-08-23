import asyncio
import base64
import binascii
import logging
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect

from .codex_client import CodexAppServer, canonical_workspace

logging.basicConfig(level=os.getenv("JARVIS_DEVELOPER_LOG_LEVEL", "INFO"))
log = logging.getLogger("jarvis-developer")
def load_token() -> str:
    direct = os.getenv("JARVIS_DEVELOPER_TOKEN", "").strip()
    if direct:
        return direct
    credentials = os.getenv("CREDENTIALS_DIRECTORY", "").strip()
    if credentials:
        path = Path(credentials) / "jarvis-developer-token"
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    return ""


TOKEN = load_token()
WORKSPACES = {
    "jarvis": Path("/home/aaron/jarvis"),
    "jarvis-wear": Path("/home/aaron/jarvis-wear"),
}
codex = CodexAppServer(os.getenv("CODEX_EXECUTABLE", "/usr/local/bin/codex"))
started = time.monotonic()
metrics = {"connections": 0, "requests": 0, "events": 0, "reconnects": 0, "active_clients": 0}
ATTACHMENT_LIMIT = 1_500_000
ATTACHMENT_DIR = Path(os.getenv(
    "JARVIS_DEVELOPER_ATTACHMENT_DIR",
    str(Path.home() / ".local" / "share" / "jarvis-developer" / "attachments"),
))
TEXT_MIMES = {"text/plain", "text/markdown", "application/json", "text/x-log"}
IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp"}
DEVELOPER_APPROVAL_POLICY = "on-request"
DEVELOPER_SANDBOX = "workspace-write"


def thread_options(path: Path) -> dict[str, str]:
    """Keep routine workspace work quiet while preserving approval for risky commands."""
    return {
        "cwd": str(path),
        "approvalPolicy": DEVELOPER_APPROVAL_POLICY,
        "sandbox": DEVELOPER_SANDBOX,
    }


def authorised(value: str | None) -> bool:
    supplied = (value or "").removeprefix("Bearer ").strip()
    return bool(TOKEN and supplied and secrets.compare_digest(TOKEN, supplied))


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not TOKEN:
        log.error("JARVIS_DEVELOPER_TOKEN is not configured")
    else:
        try:
            await codex.start()
        except Exception:
            log.exception("Initial Codex warm-up failed; requests will retry")
    yield
    await codex.stop()


app = FastAPI(title="Jarvis Developer Gateway", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy", "service": "jarvis-developer", "uptime_seconds": int(time.monotonic() - started)}


@app.get("/ready")
async def ready(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorised(authorization):
        raise HTTPException(401, "Authentication required")
    await codex.start()
    return {"ready": codex.healthy, "codex": "connected", "workspaces": list(WORKSPACES)}


@app.get("/metrics")
async def developer_metrics(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorised(authorization):
        raise HTTPException(401, "Authentication required")
    return {**metrics, "codex_ready": codex.healthy, "uptime_seconds": int(time.monotonic() - started)}


def result_or_raise(response: dict[str, Any]) -> Any:
    if "error" in response:
        raise RuntimeError(response["error"].get("message", "Codex request failed"))
    return response.get("result")


def codex_inputs(text: str, attachments: Any) -> list[dict[str, str]]:
    """Convert authenticated client content to App Server inputs without accepting paths."""
    inputs: list[dict[str, str]] = [{"type": "text", "text": text}]
    if attachments is None:
        return inputs
    if not isinstance(attachments, list) or len(attachments) > 4:
        raise ValueError("Invalid attachments")
    ATTACHMENT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    for attachment in attachments:
        if not isinstance(attachment, dict):
            raise ValueError("Invalid attachment")
        name = Path(str(attachment.get("name", "attachment"))).name[:100]
        mime = str(attachment.get("mime", "application/octet-stream")).lower()
        encoded = str(attachment.get("data", ""))
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("Invalid attachment encoding") from None
        if not content or len(content) > ATTACHMENT_LIMIT:
            raise ValueError("Attachment is empty or too large")
        if mime in IMAGE_MIMES:
            suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[mime]
            path = ATTACHMENT_DIR / f"{uuid.uuid4().hex}{suffix}"
            path.write_bytes(content)
            path.chmod(0o600)
            inputs.append({"type": "localImage", "path": str(path)})
        elif mime in TEXT_MIMES:
            body = content.decode("utf-8", errors="strict")
            inputs.append({"type": "text", "text": f"Attached file {name}:\n\n{body}"})
        else:
            raise ValueError("Unsupported attachment type")
    return inputs


@app.websocket("/api/developer")
async def developer_socket(socket: WebSocket) -> None:
    await socket.accept()
    metrics["connections"] += 1
    metrics["active_clients"] += 1
    try:
        first = await asyncio.wait_for(socket.receive_json(), 10)
        if first.get("type") != "auth" or not authorised("Bearer " + str(first.get("token", ""))):
            await socket.send_json({"type": "auth.error"})
            await socket.close(code=4401)
            return
        await codex.start()
        send_lock = asyncio.Lock()
        owned_threads: set[str] = set()
        pending_approvals: set[int] = set()
        request_times: list[float] = []

        async def emit(message: dict[str, Any]) -> None:
            params = message.get("params") or {}
            event_thread = str(params.get("threadId") or params.get("thread_id") or "")
            if event_thread and event_thread not in owned_threads:
                return
            if message.get("id") is not None and str(message.get("method", "")).endswith("requestApproval"):
                pending_approvals.add(int(message["id"]))
            metrics["events"] += 1
            async with send_lock:
                await socket.send_json({"type": "codex.event", "event": message})

        codex.subscribe(emit)
        await socket.send_json({"type": "auth.ok", "workspaces": [
            {"id": key, "name": "Jarvis Wear" if key == "jarvis-wear" else "Jarvis", "path": str(path)}
            for key, path in WORKSPACES.items()
        ]})
        while True:
            message = await socket.receive_json()
            now = time.monotonic()
            request_times[:] = [value for value in request_times if now - value < 60]
            if len(request_times) >= 120:
                await socket.close(code=4429, reason="Rate limit exceeded")
                return
            request_times.append(now)
            metrics["requests"] += 1
            kind = str(message.get("type", ""))
            request_id = message.get("request_id")
            if kind == "threads.list":
                _, path = canonical_workspace(str(message.get("workspace")), WORKSPACES)
                result = result_or_raise(await codex.request("thread/list", {"cwd": str(path), "limit": 50, "sortKey": "updated_at"}))
            elif kind == "threads.refresh":
                _, path = canonical_workspace(str(message.get("workspace")), WORKSPACES)
                result = result_or_raise(await codex.request("thread/list", {"cwd": str(path), "limit": 50, "sortKey": "updated_at"}))
            elif kind == "account.rate_limits":
                result = result_or_raise(await codex.request("account/rateLimits/read", {}))
            elif kind == "thread.delete":
                thread_id = str(message.get("thread_id", ""))
                if thread_id not in owned_threads:
                    raise ValueError("Thread is not owned by this session")
                result = result_or_raise(await codex.request("thread/delete", {"threadId": thread_id}))
                owned_threads.discard(thread_id)
            elif kind == "thread.start":
                _, path = canonical_workspace(str(message.get("workspace")), WORKSPACES)
                options = {**thread_options(path), "personality": "pragmatic"}
                result = result_or_raise(await codex.request("thread/start", options))
                owned_threads.add(str(result["thread"]["id"]))
            elif kind == "thread.resume":
                _, path = canonical_workspace(str(message.get("workspace")), WORKSPACES)
                options = {**thread_options(path), "threadId": str(message["thread_id"])}
                result = result_or_raise(await codex.request("thread/resume", options))
                owned_threads.add(str(message["thread_id"]))
            elif kind == "thread.name":
                if str(message["thread_id"]) not in owned_threads:
                    raise ValueError("Thread is not owned by this session")
                result = result_or_raise(await codex.request("thread/name/set", {"threadId": str(message["thread_id"]), "name": str(message["name"])[:120]}))
            elif kind == "turn.start":
                _, path = canonical_workspace(str(message.get("workspace")), WORKSPACES)
                if str(message["thread_id"]) not in owned_threads:
                    raise ValueError("Thread is not owned by this session")
                text = str(message.get("text", "")).strip()
                if not text or len(text) > 20000:
                    raise ValueError("Invalid instruction")
                inputs = codex_inputs(text, message.get("attachments"))
                result = result_or_raise(await codex.request("turn/start", {"threadId": str(message["thread_id"]), "cwd": str(path), "input": inputs}))
            elif kind == "turn.interrupt":
                if str(message["thread_id"]) not in owned_threads:
                    raise ValueError("Thread is not owned by this session")
                result = result_or_raise(await codex.request("turn/interrupt", {"threadId": str(message["thread_id"]), "turnId": str(message["turn_id"])}))
            elif kind == "approval.respond":
                decision = str(message.get("decision"))
                approval_id = int(message["codex_request_id"])
                if approval_id not in pending_approvals:
                    raise ValueError("Approval is not owned by this session")
                if decision not in {"accept", "acceptForSession", "decline", "cancel"}:
                    raise ValueError("Invalid approval decision")
                pending_approvals.remove(approval_id)
                await codex.respond(approval_id, {"decision": decision})
                result = {"accepted": True}
            else:
                raise ValueError("Unsupported developer operation")
            if kind in {"thread.start", "thread.resume", "thread.delete", "turn.start", "turn.interrupt", "approval.respond"}:
                log.info("Developer audit operation=%s workspace=%s", kind, str(message.get("workspace", "session"))[:32])
            await socket.send_json({"type": "response", "request_id": request_id, "result": result})
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:
        log.warning("Developer request failed: %s", type(exc).__name__)
        try:
            await socket.send_json({"type": "error", "message": str(exc)[:240]})
        except Exception:
            pass
    finally:
        metrics["active_clients"] = max(0, metrics["active_clients"] - 1)
        if "emit" in locals():
            codex.unsubscribe(emit)
