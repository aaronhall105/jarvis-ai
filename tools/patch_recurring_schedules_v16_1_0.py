from __future__ import annotations

import argparse
from pathlib import Path


class PatchError(RuntimeError):
    pass


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise PatchError(f"Expected exactly one {label} marker, found {count}.")
    return text.replace(old, new, 1)


def patch_main(text: str) -> str:
    if "from app.recurring_schedule_engine import RecurringScheduleEngine" not in text:
        text = replace_once(
            text,
        "from app.task_engine import TemporalActionEngine\n",
        "from app.task_engine import TemporalActionEngine\n"
        "from app.recurring_schedule_engine import RecurringScheduleEngine\n",
            "recurring-schedule import",
        )

    if "schedules = RecurringScheduleEngine(" not in text:
        text = replace_once(
            text,
            "capabilities = CapabilityGroundingEngine(core.tools)\n",
        "capabilities = CapabilityGroundingEngine(core.tools)\n"
        "schedules = RecurringScheduleEngine(\n"
        "    tools=core.tools,\n"
        "    action_engine=tasks,\n"
        "    database_path=\"/app/data/jarvis_recurring_schedules.db\",\n"
        "    enabled=_env_bool(\"JARVIS_SCHEDULES_ENABLED\", True),\n"
        "    timezone_name=_env_text(\"JARVIS_TIMEZONE\", \"Europe/London\"),\n"
        "    poll_seconds=_env_int(\"JARVIS_SCHEDULES_POLL_SECONDS\", 1),\n"
        "    misfire_grace_seconds=_env_int(\n"
        "        \"JARVIS_SCHEDULES_MISFIRE_GRACE_SECONDS\",\n"
        "        300,\n"
        "    ),\n"
        "    notify_completion=_env_bool(\n"
        "        \"JARVIS_SCHEDULES_NOTIFY_COMPLETION\",\n"
        "        True,\n"
        "    ),\n"
        ")\n\n"
        "_original_task_handle_command = tasks.handle_command\n\n"
        "async def _handle_temporal_or_recurring_command(text: str, actor: Any):\n"
        "    schedule_command = await schedules.handle_command(text, actor)\n"
        "    if schedule_command.handled:\n"
        "        return schedule_command\n"
        "    return await _original_task_handle_command(text, actor)\n\n"
        "tasks.handle_command = _handle_temporal_or_recurring_command\n",
            "schedule-engine construction",
        )

    if 'app.version = "2.4.0"' not in text:
        text = replace_once(
            text,
            'app.version = "2.3.7"',
        'app.version = "2.4.0"',
            "application version",
        )

    if "        await schedules.start()\n" not in text:
        text = replace_once(
            text,
            "        await tasks.start()\n"
        "        try:\n"
        "            yield\n"
        "        finally:\n"
        "            await tasks.stop()\n",
        "        await tasks.start()\n"
        "        await schedules.start()\n"
        "        try:\n"
        "            yield\n"
        "        finally:\n"
        "            await schedules.stop()\n"
        "            await tasks.stop()\n",
            "lifespan scheduler wiring",
        )

    if '"model": "temporal-action-engine-v16.1.0",' not in text:
        text = replace_once(
            text,
            '"model": "temporal-action-engine-v16.0.7",',
        '"model": "temporal-action-engine-v16.1.0",',
            "temporal model version",
        )

    if 'result["schedule" if command.intent.startswith("schedule") else "task"]' not in text:
        text = replace_once(
            text,
            "    if command.details is not None:\n"
        "        result[\"task\"] = command.details\n",
        "    if command.details is not None:\n"
        "        result[\"schedule\" if command.intent.startswith(\"schedule\") else \"task\"] = (\n"
        "            command.details\n"
        "        )\n",
            "deterministic result detail key",
        )

    api_marker = '# Jarvis v16.1.0 recurring schedule API\n'
    if api_marker not in text:
        text = text.rstrip() + "\n\n\n" + api_marker + '''\n@app.get("/api/schedules/status")\nasync def schedule_status() -> dict[str, Any]:\n    return await schedules.status()\n\n\n@app.get("/api/schedules")\nasync def schedule_list(\n    owner_key: str | None = None,\n    status: str | None = None,\n    limit: int = 50,\n) -> dict[str, Any]:\n    statuses = {status} if status else None\n    items = await schedules.list_schedules(\n        owner_key=owner_key,\n        statuses=statuses,\n        limit=limit,\n    )\n    return {"count": len(items), "schedules": items}\n\n\n@app.get("/api/schedules/{schedule_id}")\nasync def schedule_get(schedule_id: int) -> dict[str, Any]:\n    item = await schedules.get_schedule(schedule_id)\n    if item is None:\n        raise HTTPException(status_code=404, detail="Recurring schedule not found.")\n    return item\n\n\n@app.get("/api/schedules/{schedule_id}/runs")\nasync def schedule_runs(\n    schedule_id: int,\n    owner_key: str | None = None,\n    limit: int = 50,\n) -> dict[str, Any]:\n    items = await schedules.list_runs(\n        schedule_id,\n        owner_key=owner_key,\n        limit=limit,\n    )\n    return {"count": len(items), "runs": items}\n\n\n@app.post("/api/schedules/process")\nasync def schedule_process(\n    x_jarvis_admin_token: str | None = Header(default=None),\n) -> dict[str, Any]:\n    core._require_improvement_token(x_jarvis_admin_token)\n    count = await schedules.process_once()\n    return {"success": True, "processed": count, "status": await schedules.status()}\n\n\n@app.post("/api/schedules/{schedule_id}/pause")\nasync def schedule_pause(\n    schedule_id: int,\n    request: TaskCancelRequest,\n    x_jarvis_admin_token: str | None = Header(default=None),\n) -> dict[str, Any]:\n    core._require_improvement_token(x_jarvis_admin_token)\n    updated = await schedules.pause_schedule(\n        schedule_id,\n        owner_key=request.owner_key,\n        actor=request.actor,\n    )\n    if not updated:\n        raise HTTPException(status_code=404, detail="Active recurring schedule not found.")\n    return {"success": True, "schedule_id": schedule_id}\n\n\n@app.post("/api/schedules/{schedule_id}/resume")\nasync def schedule_resume(\n    schedule_id: int,\n    request: TaskCancelRequest,\n    x_jarvis_admin_token: str | None = Header(default=None),\n) -> dict[str, Any]:\n    core._require_improvement_token(x_jarvis_admin_token)\n    updated = await schedules.resume_schedule(\n        schedule_id,\n        owner_key=request.owner_key,\n        actor=request.actor,\n    )\n    if not updated:\n        raise HTTPException(status_code=404, detail="Paused recurring schedule not found.")\n    return {"success": True, "schedule_id": schedule_id}\n\n\n@app.post("/api/schedules/{schedule_id}/cancel")\nasync def schedule_cancel(\n    schedule_id: int,\n    request: TaskCancelRequest,\n    x_jarvis_admin_token: str | None = Header(default=None),\n) -> dict[str, Any]:\n    core._require_improvement_token(x_jarvis_admin_token)\n    updated = await schedules.cancel_schedule(\n        schedule_id,\n        owner_key=request.owner_key,\n        actor=request.actor,\n    )\n    if not updated:\n        raise HTTPException(status_code=404, detail="Recurring schedule not found.")\n    return {"success": True, "schedule_id": schedule_id}\n'''

    return text


def patch_task_engine(text: str) -> str:
    if '"version": "16.1.0"' in text:
        return text
    text = replace_once(
        text,
        "    v16.0.7 supports a narrow allow-list: lights, switches, TV power\n",
        "    v16.1.0 retains the verified one-off action allow-list while the\n"
        "    recurring schedule engine builds on the same exact action resolver.\n",
        "task-engine version description",
    )
    text = replace_once(
        text,
        '            "version": "16.0.7",',
        '            "version": "16.1.0",',
        "task-engine status version",
    )
    return text


def check_main(text: str) -> None:
    required = (
        'app.version = "2.4.0"',
        "schedules = RecurringScheduleEngine(",
        "tasks.handle_command = _handle_temporal_or_recurring_command",
        '"model": "temporal-action-engine-v16.1.0",',
        '@app.get("/api/schedules/status")',
        '@app.get("/api/schedules/{schedule_id}/runs")',
    )
    missing = [marker for marker in required if marker not in text]
    if missing:
        raise PatchError(f"Patched main_v16.py is missing: {missing}")


def check_task(text: str) -> None:
    if '"version": "16.1.0"' not in text:
        raise PatchError("Patched task_engine.py does not report 16.1.0.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("main_file", type=Path)
    parser.add_argument("task_file", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    main_text = args.main_file.read_text(encoding="utf-8")
    task_text = args.task_file.read_text(encoding="utf-8")

    if not args.check_only:
        main_text = patch_main(main_text)
        task_text = patch_task_engine(task_text)
        args.main_file.write_text(main_text, encoding="utf-8")
        args.task_file.write_text(task_text, encoding="utf-8")

    check_main(main_text)
    check_task(task_text)
    print(
        {
            "core_application_version": "2.4.0",
            "task_engine_version": "16.1.0",
            "recurring_schedule_engine": "16.1.0",
            "assist_version": "1.5.4",
        }
    )


if __name__ == "__main__":
    main()
