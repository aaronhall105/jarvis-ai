from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STAGE = ROOT / "updates" / "v16.1.0"

required = [
    STAGE / "bridge/app/__init__.py",
    STAGE / "bridge/app/task_engine.py",
    STAGE / "bridge/app/recurring_schedule_engine.py",
    STAGE / "bridge/tests/test_recurring_schedule_engine.py",
    STAGE / "tools/patch_recurring_schedules_v16_1_0.py",
    STAGE / "tools/install_recurring_schedules_v16_1_0.sh",
    STAGE / "release/CHANGES_V16_1_0.md",
    STAGE / "release/INSTALL_V16_1_0.md",
    STAGE / "release/MANIFEST_V16_1_0.json",
    STAGE / "release/TESTED_V16_1_0.md",
    STAGE / "docs/RECURRING_SCHEDULES_V16_1_0.md",
    ROOT / "tools/install_recurring_schedules_v16_1_0.sh",
]
missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
if missing:
    raise SystemExit(f"Missing release files: {missing}")

manifest = json.loads(
    (STAGE / "release/MANIFEST_V16_1_0.json").read_text(encoding="utf-8")
)
assert manifest["version"] == "16.1.0"
assert manifest["core_application_version"] == "2.4.0"
assert manifest["task_engine_version"] == "16.1.0"
assert manifest["recurring_schedule_engine_version"] == "16.1.0"
assert manifest["home_assistant_integration_version"] == "1.5.4"
assert manifest["home_assistant_config_entry_version"] == 2

engine = (STAGE / "bridge/app/recurring_schedule_engine.py").read_text(
    encoding="utf-8"
)
for marker in (
    '"version": "16.1.0"',
    "CREATE TABLE IF NOT EXISTS recurring_schedules",
    "CREATE TABLE IF NOT EXISTS schedule_runs",
    "misfire_grace_seconds",
    "_validate_action_available",
    "change_schedule_time",
):
    assert marker in engine, marker

print(
    {
        "version": manifest["version"],
        "core_application_version": manifest["core_application_version"],
        "assist_version": manifest["home_assistant_integration_version"],
        "required_files": len(required),
    }
)
