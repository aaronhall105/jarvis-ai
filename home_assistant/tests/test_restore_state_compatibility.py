from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
from typing import Any

import pytest


TEST_FILE = Path(__file__).resolve()
PACKAGED = (TEST_FILE.parents[1] / "custom_components").is_dir()
ROOT = TEST_FILE.parents[1] if PACKAGED else TEST_FILE.parents[2]
INTEGRATION = (
    ROOT / "custom_components/jarvis_core_conversation"
    if PACKAGED
    else ROOT / "home_assistant/custom_components/jarvis_core_conversation"
)


def _module(name: str, **values: Any) -> ModuleType:
    module = ModuleType(name)
    for key, value in values.items():
        setattr(module, key, value)
    return module


@pytest.fixture
def sensor_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    class SensorEntity:
        pass

    class CoordinatorEntity:
        def __init__(self, coordinator: Any) -> None:
            self.coordinator = coordinator

        @classmethod
        def __class_getitem__(cls, _item: Any) -> type[CoordinatorEntity]:
            return cls

    class DeviceInfo(dict[str, Any]):
        def __init__(self, **values: Any) -> None:
            super().__init__(values)

    class JarvisHomeCoordinator:
        pass

    package_name = "jarvis_restore_compat_fixture"
    package = _module(package_name)
    package.__path__ = [str(INTEGRATION)]  # type: ignore[attr-defined]
    modules = {
        "homeassistant": _module("homeassistant"),
        "homeassistant.components": _module("homeassistant.components"),
        "homeassistant.components.sensor": _module(
            "homeassistant.components.sensor", SensorEntity=SensorEntity
        ),
        "homeassistant.config_entries": _module("homeassistant.config_entries", ConfigEntry=object),
        "homeassistant.core": _module("homeassistant.core", HomeAssistant=object),
        "homeassistant.helpers": _module("homeassistant.helpers"),
        "homeassistant.helpers.device_registry": _module(
            "homeassistant.helpers.device_registry", DeviceInfo=DeviceInfo
        ),
        "homeassistant.helpers.entity_platform": _module(
            "homeassistant.helpers.entity_platform", AddEntitiesCallback=object
        ),
        "homeassistant.helpers.update_coordinator": _module(
            "homeassistant.helpers.update_coordinator", CoordinatorEntity=CoordinatorEntity
        ),
        package_name: package,
        f"{package_name}.const": _module(f"{package_name}.const", DOMAIN="jarvis_core"),
        f"{package_name}.coordinator": _module(
            f"{package_name}.coordinator", JarvisHomeCoordinator=JarvisHomeCoordinator
        ),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    name = f"{package_name}.sensor"
    spec = importlib.util.spec_from_file_location(name, INTEGRATION / "sensor.py")
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, loaded)
    spec.loader.exec_module(loaded)
    return loaded


def _coordinator(data: dict[str, Any], entry_id: str = "entry-stable") -> SimpleNamespace:
    return SimpleNamespace(data=data, entry=SimpleNamespace(entry_id=entry_id))


def test_production_integration_has_no_removed_restore_state_access() -> None:
    production = "\n".join(
        path.read_text() for path in sorted(INTEGRATION.rglob("*.py")) if path.is_file()
    )

    assert "RestoreStateData" not in production
    assert ".last_states" not in production
    assert "restore_state.async_get" not in production


def test_supported_restore_entity_apis_need_no_rewrite() -> None:
    production = "\n".join(
        path.read_text() for path in sorted(INTEGRATION.rglob("*.py")) if path.is_file()
    )

    assert "RestoreEntity" not in production
    assert "async_get_last_state" not in production
    assert "async_get_last_extra_data" not in production


def test_entity_rename_keeps_registry_identity_and_old_id_reuse_is_isolated(
    sensor_module: ModuleType,
) -> None:
    original = sensor_module.JarvisRoomSensor(_coordinator({}), "living_room", "Living Room")
    original.entity_id = "sensor.old_name"
    renamed = sensor_module.JarvisRoomSensor(_coordinator({}), "living_room", "Living Room")
    renamed.entity_id = "sensor.new_name"
    replacement = sensor_module.JarvisRoomSensor(_coordinator({}), "study", "Study")
    replacement.entity_id = "sensor.old_name"

    assert original._attr_unique_id == renamed._attr_unique_id
    assert original._attr_unique_id != replacement._attr_unique_id
    assert renamed.entity_id != original.entity_id
    assert replacement.entity_id == original.entity_id


def test_restart_uses_fresh_coordinator_truth_not_old_restored_state(
    sensor_module: ModuleType,
) -> None:
    before = sensor_module.JarvisRoomSensor(
        _coordinator(
            {
                "rooms": [
                    {
                        "area_id": "living_room",
                        "occupancy_state": "OCCUPIED",
                        "occupancy_freshness": "FRESH",
                    }
                ]
            }
        ),
        "living_room",
        "Living Room",
    )
    after = sensor_module.JarvisRoomSensor(
        _coordinator({"rooms": []}), "living_room", "Living Room"
    )

    assert before._attr_unique_id == after._attr_unique_id
    assert before.native_value == "occupied"
    assert after.native_value == "unknown"
    assert after.extra_state_attributes["freshness"] == "UNAVAILABLE"
    assert after.extra_state_attributes["source_health"] == "MISSING"


@pytest.mark.parametrize("state", ["UNKNOWN", "unavailable"])
def test_unknown_or_unavailable_is_never_projected_as_clear(
    sensor_module: ModuleType,
    state: str,
) -> None:
    sensor = sensor_module.JarvisRoomSensor(
        _coordinator(
            {
                "rooms": [
                    {
                        "area_id": "living_room",
                        "occupancy_state": state,
                        "occupancy_freshness": "STALE",
                    }
                ]
            }
        ),
        "living_room",
        "Living Room",
    )

    assert sensor.native_value in {"unknown", "unavailable"}
    assert sensor.native_value != "probably_clear"
