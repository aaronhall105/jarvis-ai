from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

os.environ.setdefault("JARVIS_DATA_DIR", "/tmp/jarvis-home-api-tests")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

from app import main
from app.home_experience import FrozenHomeAction, project_home_experience
from app.home_intelligence import GroundedHomeEntity, HomeSnapshot


def _experience():
    observed = "2026-10-08T12:00:00+00:00"
    lights = tuple(
        GroundedHomeEntity.from_state(
            {
                "entity_id": f"light.light_{index}",
                "domain": "light",
                "name": f"Light {index}",
                "state": "on",
                "available": True,
                "area_id": "living_room",
                "area_name": "Living Room",
            },
            observed,
        )
        for index in range(1, 4)
    )
    return project_home_experience(
        HomeSnapshot(
            observed_at=observed,
            entities=lights,
            presentation_entities=lights,
            areas=({"area_id": "living_room", "name": "Living Room"},),
        ),
        principal_id="aaron",
    )


@pytest.mark.asyncio
async def test_home_endpoint_is_conditional_and_freshness_sensitive(monkeypatch) -> None:
    home = _experience()
    service = SimpleNamespace(get=AsyncMock(return_value=home))
    monkeypatch.setattr(main, "home_experience_service", service)
    monkeypatch.setattr(main, "_require_mobile_integration_principal", lambda _value: "aaron")
    etag = f'"{home.revision}-LIVE"'
    conditional = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/home",
            "headers": [(b"if-none-match", etag.encode())],
        }
    )

    unchanged = await main.get_home_experience(conditional, "Bearer token")
    assert unchanged.status_code == 304
    assert unchanged.headers["etag"] == etag

    regular = Request({"type": "http", "method": "GET", "path": "/api/home", "headers": []})
    response = await main.get_home_experience(regular, "Bearer token")
    payload = json.loads(response.body)
    assert response.status_code == 200
    assert payload["lights"]["on_count"] == 3
    assert response.headers["cache-control"] == "private, no-cache"


@pytest.mark.asyncio
async def test_frozen_light_action_reports_partial_truthfully(monkeypatch) -> None:
    action = FrozenHomeAction(
        action_id="lights-off:displayed:revision",
        principal_id="aaron",
        revision="revision",
        kind="TURN_OFF_EXACT_LIGHT_SET",
        target_entity_ids=("light.one", "light.two", "light.three"),
        created_monotonic=1.0,
    )
    service = SimpleNamespace(resolve_action=lambda _principal, _action_id: action)
    execute = AsyncMock(
        return_value={
            "outcome_status": "PARTIAL",
            "requested_count": 3,
            "verified_count": 1,
            "failed_count": 1,
            "unknown_count": 1,
            "outcomes": [
                {"entity_id": "light.one", "verified": True},
                {"entity_id": "light.two", "verified": False},
                {"entity_id": "light.three", "verified": False},
            ],
        }
    )

    monkeypatch.setattr(main, "home_experience_service", service)
    monkeypatch.setattr(main, "ai", SimpleNamespace(execute_frozen_home_entity_set=execute))

    result = await main._execute_frozen_home_action(
        principal_id="aaron",
        action_id=action.action_id,
        request_id="request-1",
    )

    assert result["status"] == "PARTIAL"
    assert result["requested_count"] == 3
    assert result["verified_count"] == 1
    assert result["failed_count"] == 1
    assert result["unknown_count"] == 1
    assert result["message"] == "1 turned off. 2 failed or could not be confirmed."
    assert execute.await_args.kwargs["entity_ids"] == action.target_entity_ids
    assert execute.await_args.kwargs["allowed_domains"] == frozenset({"light"})


@pytest.mark.asyncio
async def test_frozen_action_rejects_changed_or_non_light_target(monkeypatch) -> None:
    action = FrozenHomeAction(
        action_id="lights-off:displayed:revision",
        principal_id="aaron",
        revision="revision",
        kind="TURN_OFF_EXACT_LIGHT_SET",
        target_entity_ids=("light.one", "light.missing"),
        created_monotonic=1.0,
    )
    monkeypatch.setattr(
        main,
        "home_experience_service",
        SimpleNamespace(resolve_action=lambda _principal, _action_id: action),
    )
    execute = AsyncMock(side_effect=ValueError("no longer safely controllable"))
    monkeypatch.setattr(main, "ai", SimpleNamespace(execute_frozen_home_entity_set=execute))

    with pytest.raises(ValueError, match="no longer safely controllable"):
        await main._execute_frozen_home_action(
            principal_id="aaron",
            action_id=action.action_id,
            request_id="request-2",
        )
    execute.assert_awaited_once()
