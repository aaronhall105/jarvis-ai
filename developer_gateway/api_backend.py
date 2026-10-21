"""Server-owned Responses jobs; socket subscriptions never own job lifetime."""

import asyncio
import hashlib
import json
import os
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI, APIConnectionError, APIStatusError

from .policy import POLICY, credential, redact
from .state import State
from .tools import APPROVAL, ToolRunner, schemas, validate

Sink = Callable[[dict[str, Any]], Awaitable[None]]


class ProviderUnavailable(RuntimeError):
    pass


class OpenAIResponsesDeveloperBackend:
    def __init__(self, root: Path, directory: Path, client: Any = None, key: str | None = None):
        self.key = credential() if key is None else key
        self.model = os.getenv("JARVIS_DEVELOPER_MODEL", "gpt-5.4")
        self.state = State(directory / "tasks.sqlite3")
        self.runner = ToolRunner(
            root,
            directory / "worktrees",
            Path(os.getenv("JARVIS_DEVELOPER_TEST_PYTHON", sys.executable)),
        )
        self.client: Any = client or (
            AsyncOpenAI(api_key=self.key, max_retries=0, timeout=120) if self.key else None
        )
        self.jobs: dict[str, asyncio.Task[None]] = {}
        self.sinks: set[Sink] = set()
        self.healthy = False
        self.provider_unavailable = False
        self.retry_delays = (1.0, 2.0, 4.0)
        self.approval_seconds = 900.0
        self.emergency_seconds = float(os.getenv("JARVIS_DEVELOPER_TASK_TIMEOUT_SECONDS", "3600"))

    @property
    def configured(self) -> bool:
        return bool(self.client)

    def status(self) -> dict[str, Any]:
        threads = self.state.threads()
        return {
            "api_backend_configured": self.configured,
            "api_backend_healthy": self.healthy,
            "active_jobs": sum(not job.done() for job in self.jobs.values()),
            "waiting_for_approval": sum(t["status"] == "waiting_approval" for t in threads),
            "failed_jobs": sum(t["status"] in {"failed", "paused"} for t in threads),
            "completed_jobs": sum(t["status"] == "completed" for t in threads),
        }

    async def emit(self, method: str, thread: str, **params: Any) -> None:
        event = {"method": method, "params": {"threadId": thread, **params}}
        for sink in tuple(self.sinks):
            try:
                await asyncio.wait_for(sink(event), 2)
            except Exception:
                self.sinks.discard(sink)

    async def approval_event(self, row: dict[str, Any]) -> None:
        description = row.get("description")
        if not description:
            thread = self.state.get(row["thread"])
            calls = (thread.get("pending_response") or {}).get("output", [])
            description = next(
                (
                    redact(
                        json.dumps(
                            {"tool": call["name"], "arguments": json.loads(call["arguments"])}
                        ),
                        (self.key,),
                    )
                    for call in calls
                    if call.get("call_id") == row["call_id"]
                ),
                "Operation unavailable — decline and reconcile this task",
            )
        event = {
            "id": -row["id"],
            "method": "item/commandExecution/requestApproval",
            "params": {
                "threadId": row["thread"],
                "reason": "Approve this exact operation only (not a session-wide grant):\n"
                + description,
                "command": description,
            },
        }
        for sink in tuple(self.sinks):
            try:
                await asyncio.wait_for(sink(event), 2)
            except Exception:
                self.sinks.discard(sink)

    def thread(self, identifier: str, cwd: str | None = None) -> dict[str, Any]:
        thread = self.state.get(identifier)
        if cwd and Path(thread["cwd"]).resolve() != Path(cwd).resolve():
            raise ValueError("Thread workspace mismatch")
        return thread

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "thread/start":
            if not self.configured:
                raise ProviderUnavailable("API authentication unavailable")
            return {"result": {"thread": self.public_thread(self.state.create(params["cwd"]))}}
        if method == "thread/list":
            return {
                "result": {
                    "data": [
                        {
                            k: t[k]
                            for k in (
                                "id",
                                "name",
                                "cwd",
                                "backend",
                                "createdAt",
                                "updatedAt",
                                "status",
                            )
                        }
                        for t in self.state.threads()
                        if t["cwd"] == params["cwd"]
                    ],
                    "nextCursor": None,
                }
            }
        thread = self.thread(params["threadId"], params.get("cwd"))
        identifier = thread["id"]
        if method == "thread/resume":
            # Events are replayed separately after the client owns this thread.
            return {"result": {"thread": self.public_thread(thread)}}
        if method == "thread/name/set":
            thread["name"] = str(params["name"])[:120]
            self.state.save(thread)
        elif method == "thread/delete":
            if identifier in self.jobs and not self.jobs[identifier].done():
                raise ValueError("Cancel the active task before deleting its history")
            self.state.delete(identifier)
            self.jobs.pop(identifier, None)
        elif method == "turn/interrupt":
            if params.get("turnId") != thread.get("turn_id"):
                raise ValueError("Turn ownership mismatch")
            task = self.jobs.get(identifier)
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            else:
                thread["status"] = "cancelled"
                self.state.save(thread)
            self.state.cancel_pending(identifier, thread.get("turn_id"))
        elif method in {"turn/start", "turn/retry"}:
            if identifier in self.jobs and not self.jobs[identifier].done():
                raise ValueError("A task is already active")
            if method == "turn/start":
                if thread["status"] in {"paused", "failed", "waiting_approval"}:
                    raise ValueError(
                        "Previous task is resumable; use turn.retry or cancel it first"
                    )
                text = redact(
                    "\n".join(p["text"] for p in params["input"] if p["type"] == "text"),
                    (self.key,),
                )
                if any(p["type"] != "text" for p in params["input"]):
                    raise ValueError("API Developer currently accepts text attachments only")
                turn_id = uuid.uuid4().hex
                thread.update(
                    turn_id=turn_id,
                    goal=text,
                    error=None,
                    input=[{"role": "user", "content": text}],
                    pending_response=None,
                    failures=0,
                    repeats={},
                    no_progress=0,
                    last_outcome=None,
                )
                thread["turns"].append(
                    {
                        "id": turn_id,
                        "status": "inProgress",
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                        "items": [
                            {"type": "userMessage", "text": text},
                        ],
                    }
                )
            elif thread["status"] not in {"paused", "failed", "waiting_approval"}:
                raise ValueError("No resumable task")
            thread["status"] = "running"
            self.state.save(thread)
            self.jobs[identifier] = asyncio.create_task(self.run(identifier))
            return {"result": {"turn": {"id": thread["turn_id"], "status": "inProgress"}}}
        else:
            raise ValueError("Unsupported API operation")
        return {"result": {}}

    @staticmethod
    def public_thread(thread: dict[str, Any]) -> dict[str, Any]:
        # Never return provider items/reasoning, pending prompts or tool internals.
        return {
            key: thread[key]
            for key in (
                "id",
                "backend",
                "name",
                "cwd",
                "createdAt",
                "updatedAt",
                "status",
                "turns",
                "usage",
            )
        }

    async def respond(self, identifier: int, result: dict[str, Any], owned: set[str]) -> None:
        row = self.state.call(identifier)
        if row["thread"] not in owned or row["approval"] != "pending":
            raise ValueError("Approval ownership mismatch")
        thread = self.state.get(row["thread"])
        if row["turn"] != thread.get("turn_id") or thread["status"] not in {
            "running",
            "waiting_approval",
            "paused",
        }:
            raise ValueError("Approval turn is no longer active")
        if time.time() >= row["expires"]:
            self.state.update_call(identifier, approval="expired")
            raise ValueError("Approval expired")
        decision = result.get("decision")
        if decision not in {"accept", "acceptForSession", "decline", "cancel"}:
            raise ValueError("Invalid approval")
        self.state.update_call(
            identifier,
            approval="accepted" if decision in {"accept", "acceptForSession"} else "denied",
        )

    async def provider(self, thread: dict[str, Any]) -> dict[str, Any]:
        if not self.client:
            raise ProviderUnavailable("API authentication unavailable")
        # A retry never executes partial streamed function calls.
        for attempt in range(len(self.retry_delays) + 1):
            try:
                stream = await self.client.responses.create(
                    model=self.model,
                    instructions=POLICY
                    + "\nAuthoritative task workspace state: "
                    + json.dumps(
                        {
                            "worktree": thread.get("worktree"),
                            "branch": thread.get("branch"),
                            "goal": thread.get("goal"),
                            "unresolved_error": thread.get("error"),
                        }
                    ),
                    input=thread["input"],
                    previous_response_id=thread["response_id"],
                    tools=schemas(),
                    parallel_tool_calls=False,
                    stream=True,
                    store=True,
                    context_management=[{"type": "compaction", "compact_threshold": 32000}],
                )
                request_id = getattr(getattr(stream, "response", None), "headers", {}).get(
                    "x-request-id"
                )
                if request_id:
                    thread["provider_request_id"] = str(request_id)[:100]
                    self.state.save(thread)
                final = None
                pending_text = ""
                async with stream:
                    async for event in stream:
                        if event.type == "response.output_text.delta":
                            pending_text += event.delta
                            # Redact across network chunk boundaries before UI delivery.
                            while "\n" in pending_text:
                                line, pending_text = pending_text.split("\n", 1)
                                await self.emit(
                                    "item/agentMessage/delta",
                                    thread["id"],
                                    delta=redact(line + "\n", (self.key,)),
                                )
                        elif event.type == "response.completed":
                            final = event.response.model_dump()
                        elif event.type in {"response.failed", "response.incomplete", "error"}:
                            self.healthy = False
                            self.provider_unavailable = True
                            raise ProviderUnavailable("API turn failed; state preserved")
                if not final or final.get("status") != "completed":
                    raise APIConnectionError(request=None)  # type: ignore[arg-type]
                self.healthy = True
                self.provider_unavailable = False
                return final
            except (APIConnectionError, APIStatusError) as exc:
                self.healthy = False
                self.provider_unavailable = True
                status = getattr(exc, "status_code", None)
                if status in {401, 403}:
                    raise ProviderUnavailable("API authentication unavailable") from None
                transient = status is None or status == 429 or status >= 500
                if not transient or attempt == len(self.retry_delays):
                    raise ProviderUnavailable(
                        "API temporarily unavailable; retry task later"
                    ) from None
                delay = self.retry_delays[attempt]
                headers = getattr(getattr(exc, "response", None), "headers", {})
                try:
                    delay = max(delay, float(headers.get("retry-after", delay)))
                except (TypeError, ValueError):
                    try:
                        delay = max(
                            delay,
                            parsedate_to_datetime(headers["retry-after"]).timestamp() - time.time(),
                        )
                    except (KeyError, TypeError, ValueError, OverflowError):
                        pass
                # Long provider waits pause durably rather than ignoring Retry-After.
                if delay > 120:
                    raise ProviderUnavailable(
                        "API temporarily unavailable; retry task later"
                    ) from None
                await self.emit(
                    "item/started",
                    thread["id"],
                    item={
                        "type": "mcpToolCall",
                        "id": "provider-retry",
                        "status": "inProgress",
                        "message": "API rate limited — retrying"
                        if status == 429
                        else "API retrying",
                    },
                )
                await asyncio.sleep(delay)
        raise ProviderUnavailable("API temporarily unavailable")

    async def tool(self, thread: dict[str, Any], call: dict[str, Any]) -> dict[str, Any]:
        args = json.loads(call["arguments"])
        name = call["name"]
        owner = self.state.get(thread["id"])
        if owner.get("turn_id") != thread["turn_id"] or owner["backend"] != "api":
            raise ValueError("Tool turn ownership mismatch")
        row = self.state.claim(thread["id"], thread["turn_id"], call["call_id"], name, args)
        if row["state"] == "completed":
            return json.loads(row["result"])
        if row["state"] in {"executing", "uncertain"}:
            raise ProviderUnavailable("Tool outcome uncertain; manual reconciliation required")
        if owner["status"] not in {"running", "waiting_approval"}:
            raise ValueError("Tool turn is not active")
        try:
            effect = validate(name, args)
        except ValueError:
            rejected = {"ok": False, "error": "Tool request rejected by policy"}
            self.state.update_call(row["id"], state="completed", result=json.dumps(rejected))
            return rejected
        if effect == APPROVAL:
            if not row["approval"]:
                self.state.update_call(
                    row["id"], approval="pending", expires=time.time() + self.approval_seconds
                )
            row = self.state.call(row["id"])
            thread["status"] = "waiting_approval"
            self.state.save(thread)
            row["description"] = redact(json.dumps({"tool": name, "arguments": args}), (self.key,))
            await self.approval_event(row)
            while row["approval"] == "pending" and time.time() < row["expires"]:
                await asyncio.sleep(0.2)
                row = self.state.call(row["id"])
            if row["approval"] != "accepted" or time.time() >= row["expires"]:
                result = {"ok": False, "error": "Approval denied or expired"}
                self.state.update_call(
                    row["id"],
                    state="completed",
                    result=json.dumps(result),
                    approval="denied" if row["approval"] == "denied" else "expired",
                )
                return result
        thread["status"] = "running"
        self.state.save(thread)
        self.state.update_call(row["id"], state="executing")
        await self.emit(
            "item/started",
            thread["id"],
            item={
                "id": call["call_id"],
                "type": "mcpToolCall",
                "tool": name,
                "status": "inProgress",
            },
        )
        try:
            result = await self.runner.execute(thread, name, args)
        except asyncio.CancelledError:
            self.state.update_call(row["id"], state="uncertain")
            raise
        except Exception:
            # A timeout/process error can occur after an effect; never automatically rerun it.
            self.state.update_call(row["id"], state="uncertain")
            raise ProviderUnavailable(
                "Tool did not finish safely; reconciliation required"
            ) from None
        result = json.loads(redact(json.dumps(result), (self.key,)))
        # Worktree identity must be durable before a tool outcome is exposed.
        self.state.save(thread)
        self.state.update_call(row["id"], state="completed", result=json.dumps(result))
        await self.emit(
            "item/completed",
            thread["id"],
            item={
                "id": call["call_id"],
                "type": "mcpToolCall",
                "tool": name,
                "status": "completed"
                if not isinstance(result, dict) or result.get("ok", True)
                else "failed",
            },
        )
        return result

    async def run(self, identifier: str) -> None:
        thread = self.state.get(identifier)
        await self.emit("turn/started", identifier, turn={"id": thread["turn_id"]})
        try:
            async with asyncio.timeout(self.emergency_seconds or None):
                while True:
                    response = thread.get("pending_response")
                    if response is None:
                        response = await self.provider(thread)
                        thread["pending_response"] = response
                        self.state.save(thread)
                    calls = [p for p in response["output"] if p["type"] == "function_call"]
                    if not calls:
                        text = "\n".join(
                            c.get("text", "")
                            for p in response["output"]
                            if p["type"] == "message"
                            for c in p.get("content", [])
                            if c.get("type") == "output_text"
                        )
                        if not text.strip():
                            raise ProviderUnavailable("API turn failed: no final response")
                        item = {"type": "agentMessage", "text": redact(text, (self.key,))}
                        thread["turns"][-1]["items"].append(item)
                        thread.update(
                            status="completed",
                            error=None,
                            response_id=response["id"],
                            pending_response=None,
                        )
                        usage = response.get("usage") or {}
                        for key in thread["usage"]:
                            thread["usage"][key] += max(0, int(usage.get(key, 0)))
                            thread["turns"][-1].setdefault("usage", {}).setdefault(key, 0)
                            thread["turns"][-1]["usage"][key] += max(0, int(usage.get(key, 0)))
                        thread["turns"][-1]["status"] = "completed"
                        self.state.save(thread)
                        await self.emit("item/completed", identifier, item=item)
                        break
                    results = []
                    for call in calls:
                        signature = hashlib.sha256(
                            (call["name"] + call["arguments"]).encode()
                        ).hexdigest()
                        # Count each provider call once, including across process restarts.
                        seen = thread.setdefault("seen_calls", [])
                        if call["call_id"] not in seen:
                            seen.append(call["call_id"])
                            thread["repeats"][signature] = thread["repeats"].get(signature, 0) + 1
                            self.state.save(thread)
                        if thread["repeats"][signature] > 3:
                            raise ProviderUnavailable("Repeated operation guard stopped the task")
                        try:
                            outcome = await self.tool(thread, call)
                        except (ValueError, KeyError, TypeError):
                            outcome = {"ok": False, "error": "Tool request rejected by policy"}
                        failed = isinstance(outcome, dict) and not outcome.get("ok", True)
                        thread["failures"] = thread["failures"] + 1 if failed else 0
                        digest = hashlib.sha256(
                            json.dumps(outcome, sort_keys=True).encode()
                        ).hexdigest()
                        thread["no_progress"] = (
                            thread.get("no_progress", 0) + 1
                            if digest == thread.get("last_outcome")
                            else 0
                        )
                        thread["last_outcome"] = digest
                        if thread["failures"] >= 5:
                            raise ProviderUnavailable("Repeated failed tool guard stopped the task")
                        if thread["no_progress"] >= 8:
                            raise ProviderUnavailable("No-progress guard stopped the task")
                        results.append(
                            {
                                "type": "function_call_output",
                                "call_id": call["call_id"],
                                "output": json.dumps(outcome),
                            }
                        )
                    usage = response.get("usage") or {}
                    for key in thread["usage"]:
                        thread["usage"][key] += max(0, int(usage.get(key, 0)))
                        thread["turns"][-1].setdefault("usage", {}).setdefault(key, 0)
                        thread["turns"][-1]["usage"][key] += max(0, int(usage.get(key, 0)))
                    thread.update(response_id=response["id"], input=results, pending_response=None)
                    self.state.save(thread)
        except asyncio.CancelledError:
            thread["status"] = "cancelled"
        except Exception as exc:
            thread["status"] = "failed"
            message = (
                str(exc) if isinstance(exc, ProviderUnavailable) else "Turn failed; state preserved"
            )
            thread["error"] = message
            await self.emit(
                "item/completed",
                identifier,
                item={
                    "type": "mcpToolCall",
                    "id": "turn-error",
                    "status": "failed",
                    "message": message,
                },
            )
        finally:
            thread["turns"][-1]["status"] = thread["status"]
            self.state.save(thread)
            await self.emit(
                "turn/completed",
                identifier,
                turn={
                    "id": thread["turn_id"],
                    "status": thread["status"],
                    "error": thread.get("error"),
                },
            )

    async def stop(self) -> None:
        for task in self.jobs.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.jobs.values(), return_exceptions=True)
        for identifier in self.jobs:
            thread = self.state.get(identifier)
            if thread["status"] == "cancelled":
                thread["status"] = "paused"
                self.state.save(thread)
        if self.client:
            await self.client.close()
        self.state.close()
