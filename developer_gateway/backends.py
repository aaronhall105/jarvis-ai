"""Provider boundary preserving the established Jarvis thread/event vocabulary."""

import os
from pathlib import Path
from typing import Any, Protocol

from .api_backend import OpenAIResponsesDeveloperBackend
from .codex_client import CodexAppServer, EventSink


class DeveloperBackend(Protocol):
    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]: ...


class CodexAppServerDeveloperBackend(CodexAppServer):
    """Optional legacy backend; existing Codex threads retain their original identity."""

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method in {"thread/start", "thread/resume"}:
            # The selected workspace is canonical production, not a task worktree.
            # Legacy Codex must explicitly request escalation before modifying it.
            params = {**(params or {}), "sandbox": "read-only", "approvalPolicy": "on-request"}
        response = await super().request(method, params)
        thread = (response.get("result") or {}).get("thread")
        if thread:
            for turn in thread.get("turns", []):
                turn["items"] = [
                    item for item in turn.get("items", []) if item.get("type") != "reasoning"
                ]
        return response


class DeveloperBackends:
    def __init__(self, root: Path):
        self.root = root
        self.codex = CodexAppServerDeveloperBackend(
            os.getenv("CODEX_EXECUTABLE", "/usr/local/bin/codex")
        )
        self.api: OpenAIResponsesDeveloperBackend | None = None
        self.sinks: set[EventSink] = set()
        self.owned: set[str] = set()
        self.codex.subscribe(self.emit_legacy)

    async def emit_legacy(self, event: dict[str, Any]) -> None:
        method = event.get("method", "")
        allowed = {
            "turn/started",
            "turn/completed",
            "item/started",
            "item/completed",
            "item/agentMessage/delta",
            "account/rateLimits/updated",
        }
        if method not in allowed and not method.endswith("requestApproval"):
            return
        if (event.get("params") or {}).get("item", {}).get("type") == "reasoning":
            return
        for sink in tuple(self.sinks):
            try:
                await sink(event)
            except Exception:
                self.sinks.discard(sink)

    @property
    def default(self) -> str:
        value = os.getenv("JARVIS_DEVELOPER_BACKEND", "api")
        if value not in {"api", "codex", "auto"}:
            raise ValueError("Invalid Developer backend configuration")
        return value

    def api_backend(self) -> OpenAIResponsesDeveloperBackend:
        if self.api is None:
            directory = Path(
                os.getenv(
                    "JARVIS_DEVELOPER_STATE_DIR",
                    str(Path.home() / ".local/share/jarvis-developer/agent"),
                )
            )
            self.api = OpenAIResponsesDeveloperBackend(self.root, directory)
            for sink in self.sinks:
                self.api.sinks.add(sink)
        return self.api

    async def start(self) -> None:
        if self.default == "codex":
            await self.codex.start()
        else:
            await self.api_backend().runner.reconcile()

    async def stop(self) -> None:
        if self.api:
            await self.api.stop()
        await self.codex.stop()

    @property
    def healthy(self) -> bool:
        return self.api_backend().configured if self.default != "codex" else self.codex.healthy

    def status(self) -> dict[str, Any]:
        return {
            "developer_backend_default": self.default,
            **self.api_backend().status(),
            "codex_backend_available": Path(self.codex.executable).is_file(),
            "codex_backend_healthy": self.codex.healthy,
        }

    def subscribe(self, sink: EventSink) -> None:
        self.sinks.add(sink)
        self.api_backend().sinks.add(sink)

    def unsubscribe(self, sink: EventSink) -> None:
        self.sinks.discard(sink)
        if self.api:
            self.api.sinks.discard(sink)

    async def replay(self, thread_id: str) -> None:
        if thread_id.startswith("api_"):
            for row in self.api_backend().state.pending(thread_id):
                await self.api_backend().approval_event(row)

    async def respond(self, request_id: int, result: dict[str, Any]) -> None:
        if request_id < 0:
            await self.api_backend().respond(-request_id, result, self.owned)
        else:
            await self.codex.respond(request_id, result)

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        identifier = str(params.get("threadId", ""))
        if identifier:
            backend: DeveloperBackend = (
                self.api_backend() if identifier.startswith("api_") else self.codex
            )
        elif method == "thread/list":
            api = await self.api_backend().request(method, params)
            # Listing API history must not depend on Codex availability.
            try:
                legacy = await self.codex.request(method, params)
                api["result"]["data"].extend((legacy.get("result") or {}).get("data") or [])
            except Exception:
                pass
            return api
        elif method == "account/rateLimits/read":
            if self.default != "codex":
                return {
                    "result": {
                        "backend": "api",
                        "billing": "API usage billed",
                        "rateLimits": {},
                        "usage": self.api_backend().status(),
                    }
                }
            backend = self.codex
        else:
            selected = self.default
            if selected == "auto":
                api_backend = self.api_backend()
                selected = (
                    "api"
                    if api_backend.configured and not api_backend.provider_unavailable
                    else "codex"
                )
            backend = self.api_backend() if selected == "api" else self.codex
        result = await backend.request(method, params)
        thread = (result.get("result") or {}).get("thread")
        if thread:
            self.owned.add(thread["id"])
        return result
