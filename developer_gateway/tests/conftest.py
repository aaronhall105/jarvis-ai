"""Tests must never attempt to own a real operator's Developer journal/key."""

import pytest
from contextlib import AsyncExitStack

from developer_gateway.api_backend import OpenAIResponsesDeveloperBackend


@pytest.fixture(autouse=True)
def isolated_developer_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DEVELOPER_STATE_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("JARVIS_DEVELOPER_API_KEY_FILE", str(tmp_path / "missing-key"))


@pytest.fixture
async def backend_factory():
    """Own test backends through teardown, including failed assertions."""
    async with AsyncExitStack() as cleanup:

        def create(*args, **kwargs):
            backend = OpenAIResponsesDeveloperBackend(*args, **kwargs)
            cleanup.push_async_callback(backend.stop)
            return backend

        yield create
