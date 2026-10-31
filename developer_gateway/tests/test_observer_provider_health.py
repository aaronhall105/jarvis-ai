import pytest

from developer_gateway import autonomy_host as host
from developer_gateway.autonomy import Governor
from developer_gateway.tests.test_api_backend import Client, response, start, status_error


@pytest.mark.asyncio
async def test_configuration_alone_is_not_provider_success(tmp_path, backend_factory):
    backend = backend_factory(tmp_path, tmp_path / "agent", client=Client(response()), key="")
    assert backend.status()["api_backend_configured"] is True
    assert backend.status()["api_backend_healthy"] is False
    assert backend.status()["api_backend_health"] == "UNKNOWN"
    await start(backend)
    assert backend.status()["api_backend_health"] == "HEALTHY"
    backend.client = Client(status_error(401))
    await start(backend)
    assert backend.status()["api_backend_health"] == "UNHEALTHY"
    await backend.stop()
    restored = backend_factory(tmp_path, tmp_path / "agent", client=Client(response()), key="")
    assert restored.status()["api_backend_health"] == "UNKNOWN"


@pytest.mark.parametrize(
    "configured,healthy,classification,expected",
    [
        (True, False, "UNKNOWN", None),
        (True, False, None, None),
        (True, False, "UNHEALTHY", False),
        (True, True, "HEALTHY", True),
        (True, True, "UNKNOWN", None),
        (True, False, "HEALTHY", None),
        (True, True, "invented", None),
        (False, False, "UNKNOWN", False),
    ],
)
def test_observer_never_invents_failure_from_untested_configuration(
    monkeypatch, configured, healthy, classification, expected
):
    monkeypatch.setattr(
        host,
        "_read",
        lambda _: {
            "status": "healthy",
            "api_backend_configured": configured,
            "api_backend_healthy": healthy,
            "api_backend_health": classification,
        },
    )
    readings, unavailable = host.collect_report()
    assert readings.get("api_developer") is expected
    assert ("developer" in unavailable) == (expected is None)


def test_untested_provider_does_not_create_failure_goal_or_verified_capability(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        host,
        "_read",
        lambda _: {
            "status": "healthy",
            "api_backend_configured": True,
            "api_backend_healthy": False,
            "api_backend_health": "UNKNOWN",
        },
    )
    governor = Governor(tmp_path / "observer.sqlite3")
    try:
        governor.observe(*host.collect_report())
        snapshot = governor.snapshot({"state": "STOPPED", "trusted": True})
        assert snapshot["current"] is None
        assert snapshot["plans"] == []
        api = next(c for c in snapshot["capabilities"] if c["name"] == "API development service")
        assert api["verified"] is False
        assert api["state"] != "Available"
        assert snapshot["technical"]["development_execution_available"] is False
        assert governor.state.threads() == []
    finally:
        governor.close()
