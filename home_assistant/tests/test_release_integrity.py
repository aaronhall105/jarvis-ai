from __future__ import annotations

import ast
import json
import sys
from pathlib import Path


def _config_entry_version(config_flow: Path) -> int:
    module = ast.parse(config_flow.read_text())
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == "JarvisCoreConfigFlow":
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name) and target.id == "VERSION":
                            return int(ast.literal_eval(item.value))
    raise AssertionError("JarvisCoreConfigFlow.VERSION was not found")


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
    integration = root / "custom_components" / "jarvis_core_conversation"
    manifest = json.loads((integration / "manifest.json").read_text())
    assert manifest["version"] == "1.5.4", manifest

    major_version = _config_entry_version(integration / "config_flow.py")
    init_text = (integration / "__init__.py").read_text()
    # Major version 2 is the deployed schema. Any future major bump must ship a
    # migration handler or the existing Jarvis agent will fail to load.
    assert major_version == 2 or "async_migrate_entry" in init_text, {
        "config_entry_version": major_version,
        "migration_handler": "async_migrate_entry" in init_text,
    }

    required = {
        "__init__.py",
        "config_flow.py",
        "audio_gate.py",
        "closure.py",
        "const.py",
        "conversation.py",
        "manifest.json",
        "streaming.py",
        "translations/en.json",
    }
    found = {
        str(path.relative_to(integration))
        for path in integration.rglob("*")
        if path.is_file()
    }
    missing = required - found
    assert not missing, {"missing": sorted(missing)}

    conversation_text = (integration / "conversation.py").read_text()
    prepare_block = conversation_text.split("async def async_prepare", 1)[1].split(
        "async def _async_handle_message", 1
    )[0]
    handle_block = conversation_text.split("async def _async_handle_message", 1)[1]
    assert "self._audio_gate.evaluate" not in prepare_block
    assert "self._audio_gate.evaluate" in handle_block
    assert "self._audio_gate.arm" in handle_block

    const_text = (integration / "const.py").read_text()
    config_flow_text = (integration / "config_flow.py").read_text()
    assert 'DEFAULT_FOLLOW_UP_MODE = FOLLOW_UP_SMART' in const_text
    assert 'DEFAULT_FOLLOW_UP_WINDOW = 12' in const_text
    assert 'saved[CONF_AUDIO_GATE_MIGRATED] = True' in config_flow_text

    print({
        "integration_version": manifest["version"],
        "config_entry_version": major_version,
        "required_files": len(required),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
