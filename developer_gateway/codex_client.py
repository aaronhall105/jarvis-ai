import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("jarvis-developer.codex")
EventSink = Callable[[dict[str, Any]], Awaitable[None]]


class CodexAppServer:
    """One warm, restartable Codex App Server using its supported stdio protocol."""

    def __init__(self, executable: str = "/usr/local/bin/codex") -> None:
        self.executable = executable
        self.process: asyncio.subprocess.Process | None = None
        self.reader_task: asyncio.Task[None] | None = None
        self.stderr_task: asyncio.Task[None] | None = None
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.sinks: set[EventSink] = set()
        self.next_id = 1
        self.lock = asyncio.Lock()
        self.started_at = 0.0

    async def start(self) -> None:
        async with self.lock:
            if self.process and self.process.returncode is None:
                return
            self.process = await asyncio.create_subprocess_exec(
                self.executable, "app-server", "--stdio",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self.reader_task = asyncio.create_task(self._read_loop())
            self.stderr_task = asyncio.create_task(self._drain_stderr())
            response = await self.request("initialize", {
                "clientInfo": {"name": "jarvis_android", "title": "Jarvis Developer", "version": "1.0.0"}
            })
            if "error" in response:
                raise RuntimeError(response["error"].get("message", "Codex initialization failed"))
            await self.notify("initialized", {})
            self.started_at = asyncio.get_running_loop().time()
            log.info("Codex App Server ready pid=%s", self.process.pid)

    async def stop(self) -> None:
        async with self.lock:
            process, self.process = self.process, None
            if process and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    process.kill()
            if self.reader_task:
                self.reader_task.cancel()
                self.reader_task = None
            if self.stderr_task:
                self.stderr_task.cancel()
                self.stderr_task = None

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method != "initialize":
            await self.start()
        process = self.process
        if not process or not process.stdin:
            raise RuntimeError("Codex App Server unavailable")
        request_id = self.next_id
        self.next_id += 1
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        payload: dict[str, Any] = {"method": method, "id": request_id}
        if params is not None:
            payload["params"] = params
        process.stdin.write((json.dumps(payload, separators=(",", ":")) + "\n").encode())
        await process.stdin.drain()
        try:
            return await asyncio.wait_for(future, 60)
        finally:
            self.pending.pop(request_id, None)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        process = self.process
        if not process or not process.stdin:
            raise RuntimeError("Codex App Server unavailable")
        process.stdin.write((json.dumps({"method": method, "params": params}) + "\n").encode())
        await process.stdin.drain()

    async def respond(self, request_id: int, result: dict[str, Any]) -> None:
        process = self.process
        if not process or not process.stdin:
            raise RuntimeError("Codex App Server unavailable")
        process.stdin.write((json.dumps({"id": request_id, "result": result}) + "\n").encode())
        await process.stdin.drain()

    async def _read_loop(self) -> None:
        assert self.process and self.process.stdout
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                request_id = message.get("id")
                if request_id in self.pending and ("result" in message or "error" in message):
                    future = self.pending[request_id]
                    if not future.done():
                        future.set_result(message)
                    continue
                for sink in tuple(self.sinks):
                    try:
                        await sink(message)
                    except Exception:
                        log.exception("Developer client event delivery failed")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Codex App Server reader failed")
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("Codex App Server disconnected"))

    async def _drain_stderr(self) -> None:
        assert self.process and self.process.stderr
        try:
            while line := await self.process.stderr.readline():
                # App Server diagnostics can contain user material; record only that output occurred.
                log.debug("Codex App Server diagnostic bytes=%d", len(line))
        except asyncio.CancelledError:
            raise

    def subscribe(self, sink: EventSink) -> None:
        self.sinks.add(sink)

    def unsubscribe(self, sink: EventSink) -> None:
        self.sinks.discard(sink)

    @property
    def healthy(self) -> bool:
        return bool(self.process and self.process.returncode is None and self.started_at)


def canonical_workspace(candidate: str, allowed: dict[str, Path]) -> tuple[str, Path]:
    if candidate not in allowed:
        raise ValueError("Workspace is not allowed")
    path = allowed[candidate].resolve(strict=True)
    if path not in {item.resolve(strict=True) for item in allowed.values()}:
        raise ValueError("Workspace path is not allowed")
    return candidate, path
