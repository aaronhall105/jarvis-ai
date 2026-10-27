"""The opt-in live harness must bound real-provider spending independently."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tools.certify_developer_recovery import BoundedClient


@pytest.mark.asyncio
async def test_probe_caps_requests_and_output_without_changing_model():
    create = AsyncMock(return_value="stream")
    sdk = SimpleNamespace(responses=SimpleNamespace(create=create), close=AsyncMock())
    bounded = BoundedClient(sdk)
    for _ in range(12):
        assert await bounded.create(model="configured-model", stream=True) == "stream"
    with pytest.raises(RuntimeError, match="limit"):
        await bounded.create(model="configured-model", stream=True)
    assert create.await_count == 12
    assert create.call_args.kwargs == {
        "model": "configured-model",
        "stream": True,
        "max_output_tokens": 2048,
    }
    await bounded.close()
    sdk.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_provider_attempt_consumes_probe_budget_without_retry():
    create = AsyncMock(side_effect=RuntimeError("fixture provider failure"))
    sdk = SimpleNamespace(responses=SimpleNamespace(create=create), close=AsyncMock())
    bounded = BoundedClient(sdk)
    with pytest.raises(RuntimeError, match="fixture provider failure"):
        await bounded.create(model="configured-model")
    assert bounded.requests == 1 and create.await_count == 1
