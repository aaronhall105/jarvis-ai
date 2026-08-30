from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.proactive_orchestrator import ProactiveOrchestrator


@dataclass
class Actor:
    user_key: str = "aaron"
    display_name: str = "Aaron"


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class FakeAwareness:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.delivered: list[int] = []
        self.active_summary = "Nothing user-facing appears to be left on in the flat."

    async def recent_events(self, **_: Any) -> list[dict[str, Any]]:
        return list(reversed(self.events))

    async def mark_proactive_delivered(self, event_id: int) -> bool:
        self.delivered.append(event_id)
        return True

    async def active_devices_summary(self) -> tuple[str, list[dict[str, Any]]]:
        return self.active_summary, []


class FakeTools:
    def __init__(self) -> None:
        self.states: list[dict[str, Any]] = []
        self.entity_states: dict[str, str] = {}
        self.announcements: list[dict[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []

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

    async def announce_message(self, target: str, message: str) -> dict[str, Any]:
        result = {"success": True, "target": target, "message": message}
        self.announcements.append(result)
        return result

    async def send_mobile_notification(
        self,
        recipient: str,
        message: str,
        title: str = "Jarvis",
    ) -> dict[str, Any]:
        result = {
            "success": True,
            "recipient": recipient,
            "message": message,
            "title": title,
        }
        self.notifications.append(result)
        return result


def event(
    event_id: int,
    event_type: str,
    summary: str,
    importance: int,
    entity_id: str = "sensor.test",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "occurred_at": "2026-07-26T12:00:00+00:00",
        "event_type": event_type,
        "entity_id": entity_id,
        "summary": summary,
        "importance": importance,
        "payload": {},
        **extra,
    }


def make_orchestrator(
    tmp_path: Path,
    awareness: FakeAwareness,
    tools: FakeTools,
    clock: Clock,
    **kwargs: Any,
) -> ProactiveOrchestrator:
    return ProactiveOrchestrator(
        awareness=awareness,
        tools=tools,
        database_path=str(tmp_path / "proactive.db"),
        enabled=True,
        timezone_name="Europe/London",
        quiet_start="22:30",
        quiet_end="07:00",
        process_existing_events=True,
        now_fn=clock.now,
        opening_delay_seconds=30,
        camera_offline_seconds=30,
        camera_scan_seconds=10,
        escalation_seconds=30,
        duplicate_cooldown_seconds=30,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_washing_finished_announces_when_someone_is_home(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc))
    awareness = FakeAwareness()
    tools = FakeTools()
    tools.states = [
        {"domain": "person", "entity_id": "person.aaron", "name": "Aaron", "state": "home"}
    ]
    awareness.events.append(
        event(1, "washing_finished", "The washing machine finished.", 80, "sensor.washer")
    )
    orchestrator = make_orchestrator(tmp_path, awareness, tools, clock)

    await orchestrator.process_once()

    assert len(tools.announcements) == 1
    assert tools.notifications == []
    alerts = await orchestrator.list_alerts()
    assert alerts[0]["status"] == "delivered"
    assert alerts[0]["severity"] == "high"


@pytest.mark.asyncio
async def test_washing_finished_notifies_aaron_when_nobody_is_home(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc))
    awareness = FakeAwareness()
    tools = FakeTools()
    awareness.events.append(
        event(1, "washing_finished", "The washing machine finished.", 80, "sensor.washer")
    )
    orchestrator = make_orchestrator(tmp_path, awareness, tools, clock)

    await orchestrator.process_once()

    assert tools.announcements == []
    assert tools.notifications[0]["recipient"] == "aaron"


@pytest.mark.asyncio
async def test_critical_safety_alert_uses_speaker_and_both_phones_during_quiet_hours(
    tmp_path: Path,
) -> None:
    clock = Clock(datetime(2026, 7, 26, 23, 0, tzinfo=timezone.utc))
    awareness = FakeAwareness()
    tools = FakeTools()
    tools.states = [
        {"domain": "person", "entity_id": "person.aaron", "name": "Aaron", "state": "home"}
    ]
    awareness.events.append(
        event(1, "safety_alert", "Smoke alarm reported an alert.", 100, "binary_sensor.smoke")
    )
    orchestrator = make_orchestrator(tmp_path, awareness, tools, clock)

    await orchestrator.process_once()

    assert len(tools.announcements) == 1
    assert tools.notifications[0]["recipient"] == "both"
    alerts = await orchestrator.list_alerts()
    assert alerts[0]["severity"] == "critical"
    assert alerts[0]["next_escalation_at"] is not None


@pytest.mark.asyncio
async def test_occupancy_while_away_becomes_critical_mobile_alert(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc))
    awareness = FakeAwareness()
    tools = FakeTools()
    awareness.events.append(
        event(
            1,
            "occupancy_detected",
            "Occupancy was detected in Living Room.",
            30,
            "binary_sensor.living_room_motion",
            area_name="Living Room",
        )
    )
    orchestrator = make_orchestrator(tmp_path, awareness, tools, clock)

    await orchestrator.process_once()

    assert tools.notifications[0]["recipient"] == "both"
    assert "nobody is home" in tools.notifications[0]["message"]
    assert tools.announcements == []


@pytest.mark.asyncio
async def test_opening_alert_waits_for_duration_and_cancels_when_closed(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc))
    awareness = FakeAwareness()
    tools = FakeTools()
    tools.states = [
        {"domain": "person", "entity_id": "person.aaron", "name": "Aaron", "state": "home"}
    ]
    tools.entity_states["binary_sensor.front_door"] = "on"
    awareness.events.append(
        event(1, "opening_opened", "Front Door opened.", 50, "binary_sensor.front_door")
    )
    orchestrator = make_orchestrator(tmp_path, awareness, tools, clock)

    await orchestrator.process_once()
    assert tools.announcements == []

    awareness.events.append(
        event(2, "opening_closed", "Front Door closed.", 35, "binary_sensor.front_door")
    )
    tools.entity_states["binary_sensor.front_door"] = "off"
    clock.advance(31)
    await orchestrator.process_once()

    assert tools.announcements == []
    status = await orchestrator.status()
    assert status["conditions"] == 0


@pytest.mark.asyncio
async def test_opening_still_open_after_delay_generates_high_alert(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc))
    awareness = FakeAwareness()
    tools = FakeTools()
    tools.states = [
        {"domain": "person", "entity_id": "person.aaron", "name": "Aaron", "state": "home"}
    ]
    tools.entity_states["binary_sensor.front_door"] = "on"
    awareness.events.append(
        event(1, "opening_opened", "Front Door opened.", 50, "binary_sensor.front_door")
    )
    orchestrator = make_orchestrator(tmp_path, awareness, tools, clock)

    await orchestrator.process_once()
    clock.advance(31)
    await orchestrator.process_once()

    assert len(tools.announcements) == 1
    assert "still open" in tools.announcements[0]["message"]


@pytest.mark.asyncio
async def test_normal_alert_is_held_during_quiet_hours(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 7, 26, 23, 0, tzinfo=timezone.utc))
    awareness = FakeAwareness()
    tools = FakeTools()
    tools.states = [
        {"domain": "person", "entity_id": "person.aaron", "name": "Aaron", "state": "home"}
    ]
    awareness.events.append(
        event(1, "person_arrived", "Amber arrived home.", 75, "person.amber", person_key="amber")
    )
    orchestrator = make_orchestrator(tmp_path, awareness, tools, clock)

    await orchestrator.process_once()

    assert tools.announcements == []
    assert tools.notifications == []
    alerts = await orchestrator.list_alerts()
    assert alerts[0]["status"] == "suppressed"


@pytest.mark.asyncio
async def test_acknowledgement_cancels_escalation(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc))
    awareness = FakeAwareness()
    tools = FakeTools()
    awareness.events.append(
        event(1, "safety_alert", "Water leak detected.", 100, "binary_sensor.leak")
    )
    orchestrator = make_orchestrator(tmp_path, awareness, tools, clock)

    await orchestrator.process_once()
    command = await orchestrator.handle_command("Thanks", Actor())
    clock.advance(61)
    await orchestrator.process_once()

    assert command.handled is True
    assert command.intent == "proactive_acknowledge"
    assert len(tools.notifications) == 1
    alerts = await orchestrator.list_alerts()
    assert alerts[0]["status"] == "acknowledged"
    assert alerts[0]["next_escalation_at"] is None


@pytest.mark.asyncio
async def test_snooze_command_redelivers_after_delay(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc))
    awareness = FakeAwareness()
    tools = FakeTools()
    awareness.events.append(
        event(
            1, "battery_low", "Aaron phone battery fell to 10%.", 65, "sensor.aaron_phone_battery"
        )
    )
    orchestrator = make_orchestrator(tmp_path, awareness, tools, clock)

    await orchestrator.process_once()
    command = await orchestrator.handle_command("Remind me again in one minute", Actor())
    assert command.intent == "proactive_snooze"
    assert len(tools.notifications) == 1

    clock.advance(61)
    await orchestrator.process_once()
    assert len(tools.notifications) == 2


@pytest.mark.asyncio
async def test_camera_offline_timer_is_not_reset_by_each_scan(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc))
    awareness = FakeAwareness()
    tools = FakeTools()
    tools.states = [
        {
            "domain": "camera",
            "entity_id": "camera.front_door",
            "name": "Front Door Camera",
            "state": "unavailable",
        }
    ]
    tools.entity_states["camera.front_door"] = "unavailable"
    orchestrator = make_orchestrator(tmp_path, awareness, tools, clock)

    await orchestrator.process_once()
    clock.advance(15)
    await orchestrator.process_once()
    clock.advance(16)
    await orchestrator.process_once()

    assert tools.notifications[0]["recipient"] == "aaron"
    assert "offline" in tools.notifications[0]["message"]

    clock.advance(31)
    await orchestrator.process_once()
    alerts = await orchestrator.list_alerts()
    assert len(alerts) == 1
