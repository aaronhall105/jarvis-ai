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
        self._message_id_lock = asyncio.Lock()

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

    async def _next_id(self) -> int:
        async with self._message_id_lock:
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
                auth_response.get(
                    "message",
                    "Home Assistant authentication failed",
                )
            )

    async def send_command(
        self,
        command: dict[str, Any],
    ) -> Any:
        if not self.token:
            raise HomeAssistantError(
                "HOME_ASSISTANT_TOKEN is missing"
            )

        async with websockets.connect(
            self.websocket_url,
            open_timeout=10,
            close_timeout=5,
            ping_interval=20,
            ping_timeout=20,
        ) as websocket:
            await self._authenticate(websocket)

            message_id = await self._next_id()

            payload = {
                "id": message_id,
                **command,
            }

            await websocket.send(json.dumps(payload))

            while True:
                response = json.loads(await websocket.recv())

                if response.get("id") != message_id:
                    continue

                if not response.get("success"):
                    error = response.get("error", {})

                    raise HomeAssistantError(
                        error.get(
                            "message",
                            f"Home Assistant command failed: "
                            f"{command.get('type', 'unknown')}",
                        )
                    )

                return response.get("result")

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
            logger.exception(
                "Home Assistant connection test failed"
            )

            return HomeAssistantStatus(
                connected=False,
                message=str(exc),
            )

    async def get_states(self) -> list[dict[str, Any]]:
        result = await self.send_command(
            {
                "type": "get_states",
            }
        )

        if not isinstance(result, list):
            raise HomeAssistantError(
                "Home Assistant returned an invalid states response"
            )

        return result

    async def call_service(
        self,
        domain: str,
        service: str,
        *,
        entity_ids: list[str] | None = None,
        service_data: dict[str, Any] | None = None,
    ) -> Any:
        command: dict[str, Any] = {
            "type": "call_service",
            "domain": domain,
            "service": service,
            "service_data": service_data or {},
        }

        if entity_ids:
            command["target"] = {
                "entity_id": entity_ids,
            }

        return await self.send_command(command)


async def connection_test_with_timeout(
    client: HomeAssistantClient,
) -> HomeAssistantStatus:
    try:
        return await asyncio.wait_for(
            client.test_connection(),
            timeout=15,
        )
    except TimeoutError:
        return HomeAssistantStatus(
            connected=False,
            message="Connection timed out",
        )
