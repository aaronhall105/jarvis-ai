import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import websockets

logger = logging.getLogger("jarvis-core.home-assistant")


class HomeAssistantError(RuntimeError):
    pass


@dataclass
class HomeAssistantStatus:
    connected: bool
    message: str


class HomeAssistantClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._message_id = 0

    @property
    def websocket_url(self) -> str:
        parsed = urlparse(self.base_url)

        if parsed.scheme == "https":
            scheme = "wss"
        elif parsed.scheme == "http":
            scheme = "ws"
        else:
            raise HomeAssistantError(
                "HOME_ASSISTANT_URL must start with http:// or https://"
            )

        return f"{scheme}://{parsed.netloc}/api/websocket"

    def _next_id(self) -> int:
        self._message_id += 1
        return self._message_id

    async def _authenticate(self, websocket: Any) -> None:
        first_message = json.loads(await websocket.recv())

        if first_message.get("type") != "auth_required":
            raise HomeAssistantError(
                f"Unexpected authentication response: {first_message}"
            )

        await websocket.send(
            json.dumps(
                {
                    "type": "auth",
                    "access_token": self.token,
                }
            )
        )

        auth_response = json.loads(await websocket.recv())

        if auth_response.get("type") != "auth_ok":
            raise HomeAssistantError(
                auth_response.get("message", "Home Assistant authentication failed")
            )

    async def test_connection(self) -> HomeAssistantStatus:
        if not self.token:
            return HomeAssistantStatus(
                connected=False,
                message="HOME_ASSISTANT_TOKEN is missing",
            )

        try:
            async with websockets.connect(
                self.websocket_url,
                open_timeout=10,
                close_timeout=5,
                ping_interval=20,
                ping_timeout=20,
            ) as websocket:
                await self._authenticate(websocket)

            return HomeAssistantStatus(
                connected=True,
                message="Authenticated successfully",
            )

        except Exception as exc:
            logger.exception("Home Assistant connection test failed")
            return HomeAssistantStatus(
                connected=False,
                message=str(exc),
            )

    async def get_states(self) -> list[dict[str, Any]]:
        async with websockets.connect(
            self.websocket_url,
            open_timeout=10,
            close_timeout=5,
            ping_interval=20,
            ping_timeout=20,
        ) as websocket:
            await self._authenticate(websocket)

            message_id = self._next_id()

            await websocket.send(
                json.dumps(
                    {
                        "id": message_id,
                        "type": "get_states",
                    }
                )
            )

            while True:
                response = json.loads(await websocket.recv())

                if response.get("id") != message_id:
                    continue

                if not response.get("success"):
                    raise HomeAssistantError(
                        response.get("error", {}).get(
                            "message",
                            "Failed to retrieve Home Assistant states",
                        )
                    )

                return response.get("result", [])


async def connection_test_with_timeout(
    client: HomeAssistantClient,
) -> HomeAssistantStatus:
    try:
        return await asyncio.wait_for(client.test_connection(), timeout=15)
    except TimeoutError:
        return HomeAssistantStatus(
            connected=False,
            message="Connection timed out",
        )
