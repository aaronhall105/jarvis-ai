from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.proactive_orchestrator import ProactiveOrchestrator


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class Awareness:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.summary = "Nothing user-facing appears to be left on in the flat."

    async def recent_events(self, **_: Any) -> list[dict[str, Any]]:
        return list(reversed(self.events))

    async def mark_proactive_delivered(self, event_id: int) -> bool:
        return True

    async def active_devices_summary(self) -> tuple[str, list[dict[str, Any]]]:
        return self.summary, []


class Tools:
    def __init__(self) -> None:
        self.states: list[dict[str, Any]] = []
        self.entity_states: dict[str, str] = {}
        self.notifications: list[dict[str, Any]] = []
        self.announcements: list[dict[str, Any]] = []

    async def readable_entity_states(self, refresh: bool = False) -> list[dict[str, Any]]:
        return list(self.states)

    async def get_entity_state(self, entity_id: str) -> dict[str, Any]:
        return {
            "success": True,
            "entity": {
                "entity_id": entity_id,
                "state": self.entity_states.get(entity_id, "unknown"),
            },
        }

    async def send_mobile_notification(
        self, recipient: str, message: str, title: str = "Jarvis"
    ) -> dict[str, Any]:
        result = {"success": True, "recipient": recipient, "message": message, "title": title}
        self.notifications.append(result)
        return result

    async def announce_message(self, target: str, message: str) -> dict[str, Any]:
        result = {"success": True, "target": target, "message": message}
        self.announcements.append(result)
        return result


def make_event(
    event_id: int, event_type: str, entity_id: str, summary: str, **extra: Any
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "event_type": event_type,
        "entity_id": entity_id,
        "summary": summary,
        "importance": extra.pop("importance", 30),
        "payload": extra.pop("payload", {}),
        **extra,
    }


def make(tmp_path: Path, awareness: Awareness, tools: Tools, clock: Clock) -> ProactiveOrchestrator:
    return ProactiveOrchestrator(
        awareness=awareness,
        tools=tools,
        database_path=str(tmp_path / "proactive.db"),
        process_existing_events=True,
        now_fn=clock.now,
        escalation_seconds=30,
        duplicate_cooldown_seconds=30,
        max_escalations=2,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entity_id,name",
    [
        ("binary_sensor.bedroom_suitcase_occupancy", "Bedroom Suitcase occupancy"),
        ("binary_sensor.bedroom_all_occupancy", "Bedroom All occupancy"),
        ("binary_sensor.dining_table_tv_occupancy", "Dining Table TV occupancy"),
    ],
)
async def test_object_and_zone_occupancy_is_not_treated_as_intrusion(
    tmp_path: Path, entity_id: str, name: str
) -> None:
    clock = Clock()
    awareness = Awareness()
    tools = Tools()
    awareness.events.append(
        make_event(
            1,
            "occupancy_detected",
            entity_id,
            "Occupancy was detected.",
            area_name="Bedroom",
            payload={"name": name, "device_class": "occupancy"},
        )
    )
    orchestrator = make(tmp_path, awareness, tools, clock)

    await orchestrator.process_once()

    assert tools.notifications == []
    assert await orchestrator.list_alerts() == []


@pytest.mark.asyncio
async def test_person_or_motion_occupancy_can_create_one_critical_alert(tmp_path: Path) -> None:
    clock = Clock()
    awareness = Awareness()
    tools = Tools()
    entity_id = "binary_sensor.front_door_person_occupancy"
    tools.entity_states[entity_id] = "on"
    awareness.events.extend(
        [
            make_event(
                1, "occupancy_detected", entity_id, "Person detected.", area_name="Front Door"
            ),
            make_event(
                2, "occupancy_detected", entity_id, "Person detected.", area_name="Front Door"
            ),
        ]
    )
    orchestrator = make(tmp_path, awareness, tools, clock)

    await orchestrator.process_once()

    assert len(tools.notifications) == 1
    alerts = await orchestrator.list_alerts()
    assert len(alerts) == 1
    assert alerts[0]["event_type"] == "occupancy_while_away"


@pytest.mark.asyncio
async def test_occupancy_clear_resolves_alert_and_prevents_escalation(tmp_path: Path) -> None:
    clock = Clock()
    awareness = Awareness()
    tools = Tools()
    entity_id = "binary_sensor.front_door_motion"
    tools.entity_states[entity_id] = "on"
    awareness.events.append(
        make_event(1, "occupancy_detected", entity_id, "Motion detected.", area_name="Front Door")
    )
    orchestrator = make(tmp_path, awareness, tools, clock)
    await orchestrator.process_once()
    assert len(tools.notifications) == 1

    tools.entity_states[entity_id] = "off"
    awareness.events.append(
        make_event(2, "occupancy_cleared", entity_id, "Motion cleared.", area_name="Front Door")
    )
    clock.advance(61)
    await orchestrator.process_once()

    assert len(tools.notifications) == 1
    alerts = await orchestrator.list_alerts()
    assert alerts[0]["status"] == "resolved"
    assert (await orchestrator.status())["active"] == 0


@pytest.mark.asyncio
async def test_escalation_checks_live_condition_before_notifying(tmp_path: Path) -> None:
    clock = Clock()
    awareness = Awareness()
    tools = Tools()
    entity_id = "binary_sensor.hall_motion"
    tools.entity_states[entity_id] = "on"
    awareness.events.append(
        make_event(1, "occupancy_detected", entity_id, "Motion detected.", area_name="Hallway")
    )
    orchestrator = make(tmp_path, awareness, tools, clock)
    await orchestrator.process_once()

    tools.entity_states[entity_id] = "off"
    clock.advance(61)
    await orchestrator.process_once()

    assert len(tools.notifications) == 1
    assert (await orchestrator.list_alerts())[0]["status"] == "resolved"


@pytest.mark.asyncio
async def test_arrival_resolves_all_away_occupancy_alerts(tmp_path: Path) -> None:
    clock = Clock()
    awareness = Awareness()
    tools = Tools()
    motion = "binary_sensor.hall_motion"
    tools.entity_states[motion] = "on"
    awareness.events.append(
        make_event(1, "occupancy_detected", motion, "Motion detected.", area_name="Hallway")
    )
    orchestrator = make(tmp_path, awareness, tools, clock)
    await orchestrator.process_once()

    tools.states = [
        {"domain": "person", "entity_id": "person.aaron", "name": "Aaron", "state": "home"}
    ]
    awareness.events.append(
        make_event(2, "person_arrived", "person.aaron", "Aaron arrived home.", importance=75)
    )
    await orchestrator.process_once()

    occupancy = [
        a for a in await orchestrator.list_alerts() if a["event_type"] == "occupancy_while_away"
    ]
    assert occupancy[0]["status"] == "resolved"


@pytest.mark.asyncio
async def test_devices_left_on_filters_infrastructure_and_dedupes_household(tmp_path: Path) -> None:
    clock = Clock()
    awareness = Awareness()
    tools = Tools()
    awareness.summary = (
        "Yes — these are still on: Advanced SSH & Web Terminal, Bedroom Heater Plug, "
        "Mosquitto broker, Kitchen Washing Machine, Tailscale, and Whisper."
    )
    awareness.events.extend(
        [
            make_event(1, "person_left", "person.aaron", "Aaron left home.", importance=45),
            make_event(2, "person_left", "person.amber", "Amber left home.", importance=45),
        ]
    )
    orchestrator = make(tmp_path, awareness, tools, clock)

    await orchestrator.process_once()

    alerts = [a for a in await orchestrator.list_alerts() if a["event_type"] == "devices_left_on"]
    assert len(alerts) == 1
    assert alerts[0]["entity_id"] == "household"
    assert "Bedroom Heater Plug" in alerts[0]["summary"]
    assert "Kitchen Washing Machine" in alerts[0]["summary"]
    assert "Mosquitto" not in alerts[0]["summary"]
    assert alerts[0]["next_escalation_at"] is None


@pytest.mark.asyncio
async def test_one_shot_delivered_alerts_are_not_counted_active(tmp_path: Path) -> None:
    clock = Clock()
    awareness = Awareness()
    tools = Tools()
    tools.states = [
        {"domain": "person", "entity_id": "person.aaron", "name": "Aaron", "state": "home"}
    ]
    awareness.events.append(
        make_event(1, "person_arrived", "person.amber", "Amber arrived home.", importance=75)
    )
    orchestrator = make(tmp_path, awareness, tools, clock)

    await orchestrator.process_once()

    assert (await orchestrator.list_alerts())[0]["status"] == "delivered"
    assert (await orchestrator.status())["active"] == 0


@pytest.mark.asyncio
async def test_safety_clear_resolves_original_safety_alert(tmp_path: Path) -> None:
    clock = Clock()
    awareness = Awareness()
    tools = Tools()
    entity_id = "binary_sensor.leak"
    tools.entity_states[entity_id] = "on"
    awareness.events.append(
        make_event(1, "safety_alert", entity_id, "A leak was detected.", importance=100)
    )
    orchestrator = make(tmp_path, awareness, tools, clock)
    await orchestrator.process_once()

    tools.entity_states[entity_id] = "off"
    awareness.events.append(
        make_event(2, "safety_cleared", entity_id, "The leak cleared.", importance=85)
    )
    await orchestrator.process_once()

    safety = [a for a in await orchestrator.list_alerts() if a["event_type"] == "safety_alert"]
    assert safety[0]["status"] == "resolved"
