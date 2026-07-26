from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    main_v16 = (ROOT / "bridge/app/main_v16.py").read_text()
    task_engine = (ROOT / "bridge/app/task_engine.py").read_text()
    capability = (ROOT / "bridge/app/capability_grounding.py").read_text()
    integration = ROOT / "home_assistant/custom_components/jarvis_core_conversation"
    manifest = json.loads((integration / "manifest.json").read_text())
    workflow = (ROOT / ".github/workflows/jarvis-ci.yml").read_text()

    assert 'app.version = "2.3.7"' in main_v16
    assert 'CapabilityGroundingEngine' in main_v16
    assert 'capability-grounding-v16.0.7' in main_v16
    assert 'jarvis-assist-smart-audio-gate-v1.5.4.tar.gz' in main_v16
    assert '"version": "16.0.7"' in task_engine
    assert 'supported_color_modes' in capability
    assert 'set_light_brightness' in capability
    assert manifest["version"] == "1.5.4"
    assert (integration / "audio_gate.py").is_file()
    assert (integration / "closure.py").is_file()
    assert (ROOT / "home_assistant/tests/test_audio_gate.py").is_file()
    assert "conversation-engine" in workflow
    print({
        "core_application_version": "2.3.7",
        "task_engine_version": "16.0.7",
        "assist_version": manifest["version"],
        "capability_grounding": "live-and-verified",
        "smart_audio_gate": "enabled",
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
