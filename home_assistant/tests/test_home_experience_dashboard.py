from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess

import yaml


TEST_FILE = Path(__file__).resolve()
PACKAGED = (TEST_FILE.parents[1] / "dashboard").is_dir()
ROOT = TEST_FILE.parents[1] if PACKAGED else TEST_FILE.parents[2]


def _dashboard() -> Path:
    if PACKAGED:
        return ROOT / "dashboard/jarvis_alpha38_dashboard.yaml"
    return ROOT / "docs/configuration/jarvis_alpha38_dashboard.yaml"


def _integration() -> Path:
    return (
        ROOT / "custom_components/jarvis_core_conversation"
        if PACKAGED
        else (ROOT / "home_assistant/custom_components/jarvis_core_conversation")
    )


def _installer() -> Path:
    return (
        ROOT / "tools/install_jarvis_home_v1_7_0.sh"
        if PACKAGED
        else (ROOT / "home_assistant/tools/install_jarvis_home_v1_7_0.sh")
    )


def test_dashboard_yaml_is_valid_and_keeps_diagnostics_off_home() -> None:
    dashboard = yaml.safe_load(_dashboard().read_text())
    views = {item["path"]: item for item in dashboard["views"]}

    assert {"home", "rooms", "cameras", "energy", "people", "more"} <= set(views)
    assert views["living-room"]["subview"] is True
    text = str(views["home"])
    assert "sensor.jarvis_home_status" in text
    assert "sensor.jarvis_lights_on" in text
    assert "sensor.jarvis_unavailable_devices" in text
    assert "person.presence" in text
    assert "Mobile Export Tool" not in text
    assert "System / Diagnostics" not in text
    assert "System / Diagnostics" in str(views["more"])
    assert "Showing last known state" in text
    assert "camera.living_room_clear" in str(views["cameras"])
    assert "camera.living_room_fluent" not in str(views["cameras"])
    assert "light.living_room_ceiling" in str(views["living-room"])
    assert "binary_sensor.living_room_person" not in str(views["living-room"])
    assert "binary_sensor.living_room_motion" not in str(views["living-room"])


def test_sensor_bridge_uses_home_api_without_reconstructing_raw_ha_state() -> None:
    integration = _integration()
    coordinator = (integration / "coordinator.py").read_text()
    sensor = (integration / "sensor.py").read_text()

    ast.parse(coordinator)
    ast.parse(sensor)
    assert 'f"{base_url}/api/home"' in coordinator
    assert '"Authorization": f"Bearer {token}"' in coordinator
    assert "If-None-Match" in coordinator
    assert "state_changed" not in sensor
    assert "entity_registry" not in sensor
    assert "occupancy_state" in sensor
    assert "unavailable_count" in sensor


def test_installer_is_backup_first_and_never_mutates_lovelace() -> None:
    installer = _installer().read_text()

    assert 'cp -a "$TARGET" "$BACKUP_ROOT/jarvis_core_conversation"' in installer
    assert installer.index("Backing up the current Home Assistant integration") < installer.rindex(
        'rm -rf "$TARGET"'
    )
    assert "trap rollback ERR" in installer
    assert "ha core check" in installer
    assert installer.index("ha core check") < installer.rindex("RESTORE_REQUIRED=false")
    assert ".storage/lovelace" not in installer
    assert "ui-lovelace.yaml" not in installer


def test_assist_package_carries_dashboard_without_auto_installing_it() -> None:
    if PACKAGED:
        assert _dashboard().is_file()
        assert (ROOT / "dashboard/INSTALL_DASHBOARD.md").is_file()
        return
    builder = (ROOT / "tools/build_assist_package.sh").read_text()

    assert "jarvis_alpha38_dashboard.yaml" in builder
    assert "install_jarvis_home_v1_7_0.sh" in builder
    assert "install_jarvis_home_v1_6_0.sh" not in builder
    assert "INSTALL_DASHBOARD.md" in builder
    assert 'cp "$DASHBOARD" "$STAGE/dashboard/' in builder


def test_installer_restores_existing_integration_when_ha_check_fails(tmp_path: Path) -> None:
    config = tmp_path / "config"
    target = config / "custom_components/jarvis_core_conversation"
    target.mkdir(parents=True)
    (target / "conversation.py").write_text("# original conversation\n")
    (target / "manifest.json").write_text('{"version":"previous"}\n')
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ha = fake_bin / "ha"
    ha.write_text("#!/usr/bin/env bash\nexit 1\n")
    ha.chmod(0o755)
    environment = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    completed = subprocess.run(
        [
            str(_installer()),
            str(config),
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert (target / "conversation.py").read_text() == "# original conversation\n"
    assert (target / "manifest.json").read_text() == '{"version":"previous"}\n'
    assert "restoring the previous integration" in completed.stdout
