from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

required = [
    ROOT / "bridge/app/__init__.py",
    ROOT / "bridge/app/main_v16.py",
    ROOT / "bridge/app/task_engine.py",
    ROOT / "bridge/app/recurring_schedule_engine.py",
    ROOT / "bridge/app/conditional_action_engine.py",
    ROOT / "bridge/app/capability_grounding.py",
    ROOT / "bridge/app/tone_engine.py",
    ROOT / "bridge/tests/test_capability_grounding.py",
    ROOT / "bridge/tests/test_progress_experience.py",
    ROOT / "bridge/tests/test_task_engine.py",
    ROOT / "bridge/tests/test_recurring_schedule_engine.py",
    ROOT / "bridge/tests/test_conditional_action_engine.py",
    ROOT / "tools/patch_conditional_actions_v16_2_0.py",
    ROOT / "tools/install_conditional_actions_v16_2_0.sh",
    ROOT / "release/CHANGES_V16_2_0.md",
    ROOT / "release/INSTALL_V16_2_0.md",
    ROOT / "release/MANIFEST_V16_2_0.json",
    ROOT / "release/TESTED_V16_2_0.md",
    ROOT / "docs/CONDITIONAL_ACTIONS_V16_2_0.md",
]
missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
if missing:
    raise SystemExit(f"Missing v16.2.0 package files: {missing}")

manifest = json.loads((ROOT / "release/MANIFEST_V16_2_0.json").read_text(encoding="utf-8"))
if manifest.get("version") != "16.2.0":
    raise SystemExit("Manifest version is not 16.2.0")
if manifest.get("core_application_version") != "2.5.0":
    raise SystemExit("Core application version is not 2.5.0")
if manifest.get("conditional_action_engine_version") != "16.2.0":
    raise SystemExit("Conditional engine version is not 16.2.0")
if manifest.get("home_assistant_integration_version") != "1.5.4":
    raise SystemExit("Assist integration version changed unexpectedly")

main_text = (ROOT / "bridge/app/main_v16.py").read_text(encoding="utf-8")
task_text = (ROOT / "bridge/app/task_engine.py").read_text(encoding="utf-8")
condition_text = (ROOT / "bridge/app/conditional_action_engine.py").read_text(encoding="utf-8")

main_markers = [
    'app.version = "2.5.0"',
    "conditions = ConditionalActionEngine(",
    "tasks.handle_command = _handle_conditional_or_existing_command",
    "await conditions.start()",
    "await conditions.stop()",
    '"conditional-action-engine-v16.2.0"',
    '@app.get("/api/conditions/status")',
    '@app.get("/api/conditions/{rule_id}/runs")',
    '@app.post("/api/conditions/{rule_id}/cancel")',
]
missing_markers = [marker for marker in main_markers if marker not in main_text]
if missing_markers:
    raise SystemExit(f"main_v16.py missing markers: {missing_markers}")

if '"version": "16.2.0"' not in task_text:
    raise SystemExit("task_engine.py does not report v16.2.0")

condition_markers = [
    '"version": "16.2.0"',
    "CREATE TABLE IF NOT EXISTS conditional_rules",
    "CREATE TABLE IF NOT EXISTS conditional_rule_runs",
    "def _edge_matches(",
    "async def process_once(",
    "async def handle_command(",
    "notify_owner",
    "presence_leave",
    "numeric_below",
    "time_state",
    "_TIMED_CONDITION_PATTERN",
]
missing_condition_markers = [marker for marker in condition_markers if marker not in condition_text]
if missing_condition_markers:
    raise SystemExit(
        f"conditional_action_engine.py missing markers: {missing_condition_markers}"
    )

print(
    {
        "version": manifest["version"],
        "core_application_version": manifest["core_application_version"],
        "task_engine_version": manifest["task_engine_version"],
        "recurring_schedule_engine_version": manifest["recurring_schedule_engine_version"],
        "conditional_action_engine_version": manifest["conditional_action_engine_version"],
        "assist_version": manifest["home_assistant_integration_version"],
        "required_files": len(required),
    }
)
