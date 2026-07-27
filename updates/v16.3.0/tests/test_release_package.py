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
    ROOT / "bridge/app/routine_engine.py",
    ROOT / "bridge/tests/test_capability_grounding.py",
    ROOT / "bridge/tests/test_progress_experience.py",
    ROOT / "bridge/tests/test_task_engine.py",
    ROOT / "bridge/tests/test_recurring_schedule_engine.py",
    ROOT / "bridge/tests/test_conditional_action_engine.py",
    ROOT / "bridge/tests/test_routine_engine.py",
    ROOT / "tools/install_multi_step_routines_v16_3_0.sh",
    ROOT / "release/CHANGES_V16_3_0.md",
    ROOT / "release/INSTALL_V16_3_0.md",
    ROOT / "release/MANIFEST_V16_3_0.json",
    ROOT / "release/TESTED_V16_3_0.md",
    ROOT / "docs/MULTI_STEP_ROUTINES_V16_3_0.md",
]
missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
if missing:
    raise SystemExit(f"Missing release files: {missing}")

manifest = json.loads((ROOT / "release/MANIFEST_V16_3_0.json").read_text())
expected = {
    "version": "16.3.0",
    "core_application_version": "2.6.0",
    "task_engine_version": "16.3.0",
    "recurring_schedule_engine_version": "16.1.0",
    "conditional_action_engine_version": "16.2.0",
    "routine_engine_version": "16.3.0",
    "home_assistant_integration_version": "1.5.4",
    "home_assistant_config_entry_version": 2,
}
for key, value in expected.items():
    if manifest.get(key) != value:
        raise SystemExit(f"Manifest mismatch for {key}: {manifest.get(key)!r}")

main_text = (ROOT / "bridge/app/main_v16.py").read_text()
task_text = (ROOT / "bridge/app/task_engine.py").read_text()
routine_text = (ROOT / "bridge/app/routine_engine.py").read_text()
checks = {
    "Core application version": 'app.version = "2.6.0"' in main_text,
    "routine import": "from app.routine_engine import RoutineEngine" in main_text,
    "routine status endpoint": '"/api/routines/status"' in main_text,
    "task-engine version": '"version": "16.3.0"' in task_text,
    "sequence execution": 'action_type == "sequence"' in task_text,
    "routine-engine version": '"version": "16.3.0"' in routine_text,
    "owner-scoped routine database": "owner_key TEXT NOT NULL" in routine_text,
}
failed = [name for name, passed in checks.items() if not passed]
if failed:
    raise SystemExit(f"Release integrity checks failed: {failed}")

cache_files = [
    path for path in ROOT.rglob("*")
    if path.name == "__pycache__" or path.suffix == ".pyc"
]
if cache_files:
    raise SystemExit(f"Generated cache files found: {cache_files[:5]}")

print(
    {
        "version": manifest["version"],
        "core_application_version": manifest["core_application_version"],
        "task_engine_version": manifest["task_engine_version"],
        "conditional_action_engine_version": manifest["conditional_action_engine_version"],
        "routine_engine_version": manifest["routine_engine_version"],
        "assist_version": manifest["home_assistant_integration_version"],
        "required_files": len(required),
    }
)
