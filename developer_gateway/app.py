import asyncio
import logging
import os
import secrets
import time
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
            elif kind == "thread.start":
                _, path = canonical_workspace(str(message.get("workspace")), WORKSPACES)
                result = result_or_raise(await codex.request("thread/start", {"cwd": str(path), "approvalPolicy": "on-request", "sandbox": "workspace-write", "personality": "pragmatic"}))
                owned_threads.add(str(result["thread"]["id"]))
            elif kind == "thread.resume":
                _, path = canonical_workspace(str(message.get("workspace")), WORKSPACES)
                result = result_or_raise(await codex.request("thread/resume", {"threadId": str(message["thread_id"]), "cwd": str(path), "approvalPolicy": "on-request", "sandbox": "workspace-write"}))
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
                result = result_or_raise(await codex.request("turn/start", {"threadId": str(message["thread_id"]), "cwd": str(path), "input": [{"type": "text", "text": text}]}))
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
            if kind in {"thread.start", "thread.resume", "turn.start", "turn.interrupt", "approval.respond"}:
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
