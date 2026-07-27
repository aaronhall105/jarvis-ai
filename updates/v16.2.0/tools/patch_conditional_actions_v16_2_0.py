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
    if "from app.conditional_action_engine import ConditionalActionEngine" not in text:
        text = replace_once(
            text,
            "from app.recurring_schedule_engine import RecurringScheduleEngine\n",
            "from app.recurring_schedule_engine import RecurringScheduleEngine\n"
            "from app.conditional_action_engine import ConditionalActionEngine\n",
            "conditional-action import",
        )

    if "conditions = ConditionalActionEngine(" not in text:
        marker = "tasks.handle_command = _handle_temporal_or_recurring_command\n"
        addition = '''tasks.handle_command = _handle_temporal_or_recurring_command

conditions = ConditionalActionEngine(
    tools=core.tools,
    action_engine=tasks,
    database_path="/app/data/jarvis_conditional_actions.db",
    enabled=_env_bool("JARVIS_CONDITIONS_ENABLED", True),
    timezone_name=_env_text("JARVIS_TIMEZONE", "Europe/London"),
    poll_seconds=_env_int("JARVIS_CONDITIONS_POLL_SECONDS", 2),
    default_cooldown_seconds=_env_int(
        "JARVIS_CONDITIONS_DEFAULT_COOLDOWN_SECONDS",
        300,
    ),
    default_debounce_seconds=_env_int(
        "JARVIS_CONDITIONS_DEFAULT_DEBOUNCE_SECONDS",
        2,
    ),
    notify_failures=_env_bool("JARVIS_CONDITIONS_NOTIFY_FAILURES", True),
)

_original_temporal_or_recurring_handle_command = tasks.handle_command

async def _handle_conditional_or_existing_command(text: str, actor: Any):
    conditional_command = await conditions.handle_command(text, actor)
    if conditional_command.handled:
        return conditional_command
    return await _original_temporal_or_recurring_handle_command(text, actor)

tasks.handle_command = _handle_conditional_or_existing_command
'''
        text = replace_once(text, marker, addition, "conditional command routing")

    text = replace_once(
        text,
        'app.version = "2.4.0"',
        'app.version = "2.5.0"',
        "application version",
    )

    if "        await conditions.start()\n" not in text:
        text = replace_once(
            text,
            "        await tasks.start()\n"
            "        await schedules.start()\n"
            "        try:\n"
            "            yield\n"
            "        finally:\n"
            "            await schedules.stop()\n"
            "            await tasks.stop()\n",
            "        await tasks.start()\n"
            "        await schedules.start()\n"
            "        await conditions.start()\n"
            "        try:\n"
            "            yield\n"
            "        finally:\n"
            "            await conditions.stop()\n"
            "            await schedules.stop()\n"
            "            await tasks.stop()\n",
            "conditional lifespan wiring",
        )

    text = replace_once(
        text,
        '        "model": "temporal-action-engine-v16.1.0",',
        '        "model": (\n'
        '            "conditional-action-engine-v16.2.0"\n'
        '            if command.intent.startswith("condition")\n'
        '            else "temporal-action-engine-v16.2.0"\n'
        '        ),',
        "deterministic model version",
    )

    text = replace_once(
        text,
        "    if command.details is not None:\n"
        "        result[\"schedule\" if command.intent.startswith(\"schedule\") else \"task\"] = (\n"
        "            command.details\n"
        "        )\n",
        "    if command.details is not None:\n"
        "        if command.intent.startswith(\"condition\"):\n"
        "            result[\"condition\"] = command.details\n"
        "        elif command.intent.startswith(\"schedule\"):\n"
        "            result[\"schedule\"] = command.details\n"
        "        else:\n"
        "            result[\"task\"] = command.details\n",
        "deterministic result detail routing",
    )

    api_marker = "# Jarvis v16.2.0 conditional action API\n"
    if api_marker not in text:
        text = text.rstrip() + "\n\n\n" + api_marker + '''

@app.get("/api/conditions/status")
async def condition_status() -> dict[str, Any]:
    return await conditions.status()


@app.get("/api/conditions")
async def condition_list(
    owner_key: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    statuses = {status} if status else None
    items = await conditions.list_rules(
        owner_key=owner_key,
        statuses=statuses,
        limit=limit,
    )
    return {"count": len(items), "rules": items}


@app.get("/api/conditions/{rule_id}")
async def condition_get(rule_id: int) -> dict[str, Any]:
    item = await conditions.get_rule(rule_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Conditional rule not found.")
    return item


@app.get("/api/conditions/{rule_id}/runs")
async def condition_runs(
    rule_id: int,
    owner_key: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    items = await conditions.list_runs(
        rule_id,
        owner_key=owner_key,
        limit=limit,
    )
    return {"count": len(items), "runs": items}


@app.post("/api/conditions/process")
async def condition_process(
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    core._require_improvement_token(x_jarvis_admin_token)
    count = await conditions.process_once()
    return {"success": True, "processed": count, "status": await conditions.status()}


@app.post("/api/conditions/{rule_id}/pause")
async def condition_pause(
    rule_id: int,
    request: TaskCancelRequest,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    core._require_improvement_token(x_jarvis_admin_token)
    updated = await conditions.pause_rule(
        rule_id,
        owner_key=request.owner_key,
        actor=request.actor,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Active conditional rule not found.")
    return {"success": True, "rule_id": rule_id}


@app.post("/api/conditions/{rule_id}/resume")
async def condition_resume(
    rule_id: int,
    request: TaskCancelRequest,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    core._require_improvement_token(x_jarvis_admin_token)
    updated = await conditions.resume_rule(
        rule_id,
        owner_key=request.owner_key,
        actor=request.actor,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Paused conditional rule not found.")
    return {"success": True, "rule_id": rule_id}


@app.post("/api/conditions/{rule_id}/cancel")
async def condition_cancel(
    rule_id: int,
    request: TaskCancelRequest,
    x_jarvis_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    core._require_improvement_token(x_jarvis_admin_token)
    updated = await conditions.cancel_rule(
        rule_id,
        owner_key=request.owner_key,
        actor=request.actor,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Conditional rule not found.")
    return {"success": True, "rule_id": rule_id}
'''
    return text


def patch_task_engine(text: str) -> str:
    if '"version": "16.2.0"' in text:
        return text
    text = text.replace(
        "    v16.1.0 retains the verified one-off action allow-list while the\n"
        "    recurring schedule engine builds on the same exact action resolver.\n",
        "    v16.2.0 retains the verified one-off action allow-list while recurring\n"
        "    schedules and conditional rules reuse the same exact action resolver.\n",
        1,
    )
    text = replace_once(
        text,
        '            "version": "16.1.0",',
        '            "version": "16.2.0",',
        "task-engine status version",
    )
    return text


def check_main(text: str) -> None:
    required = (
        'app.version = "2.5.0"',
        "conditions = ConditionalActionEngine(",
        "tasks.handle_command = _handle_conditional_or_existing_command",
        '"conditional-action-engine-v16.2.0"',
        '@app.get("/api/conditions/status")',
        '@app.get("/api/conditions/{rule_id}/runs")',
        "await conditions.start()",
        "await conditions.stop()",
    )
    missing = [item for item in required if item not in text]
    if missing:
        raise PatchError(f"Patched main_v16.py is missing: {missing}")


def check_task(text: str) -> None:
    if '"version": "16.2.0"' not in text:
        raise PatchError("Patched task_engine.py does not report 16.2.0.")


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
            "core_application_version": "2.5.0",
            "task_engine_version": "16.2.0",
            "recurring_schedule_engine": "16.1.0",
            "conditional_action_engine": "16.2.0",
            "assist_version": "1.5.4",
        }
    )


if __name__ == "__main__":
    main()
