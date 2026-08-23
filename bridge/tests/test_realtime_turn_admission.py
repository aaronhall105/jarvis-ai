from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app.realtime_turn_ledger import (
    RealtimeTurnLedger,
)
from app.realtime_voice import (
    RealtimeVoiceConfig,
    RealtimeVoiceProxy,
)


class RecordingClient:
    def __init__(
        self,
        payloads: list[dict[str, Any]],
    ) -> None:
        self.payloads = list(payloads)
        self.messages: list[
            dict[str, Any]
        ] = []

    async def receive(
        self,
    ) -> dict[str, Any]:
        if self.payloads:
            return self.payloads.pop(0)

        return {
            "type":
                "websocket.disconnect"
        }

    async def send_json(
        self,
        payload: dict[str, Any],
    ) -> None:
        self.messages.append(payload)


class RecordingUpstream:
    def __init__(self) -> None:
        self.messages: list[
            dict[str, Any]
        ] = []

    async def send(
        self,
        payload: str,
    ) -> None:
        self.messages.append(
            json.loads(payload)
        )


def config() -> RealtimeVoiceConfig:
    return RealtimeVoiceConfig(
        enabled=True,
        api_key="test-key",
        mobile_token="test-token",
        voice_pe_token="voice-pe-token",
        model="gpt-realtime",
        voice="marin",
        user_id="aaron",
        user_name="Aaron",
        user_is_admin=True,
        transcription_prompt="",
    )


def metadata() -> dict[str, Any]:
    return {
        "client_kind": "mobile",
        "device_id": "phone-1",
        "conversation_id": "chat-1",
    }


def state() -> dict[str, Any]:
    terminal = asyncio.Event()
    terminal.set()

    return {
        "provider_epoch": 1,
        "generation": 0,
        "suppress_audio": False,
        "provider_transitioning":
            False,
        "turn_terminal_event":
            terminal,
        "openai_response_turns": {},
    }


def text_message(
    text: str,
    client_turn_id: int,
) -> dict[str, Any]:
    return {
        "text": json.dumps(
            {
                "type": "text",
                "text": text,
                "speak": False,
                "client_turn_id":
                    client_turn_id,
            }
        )
    }


def status_message(
    client_turn_id: int,
) -> dict[str, Any]:
    return {
        "text": json.dumps(
            {
                "type": "turn.status",
                "client_turn_id":
                    client_turn_id,
            }
        )
    }


async def run_reader(
    proxy: RealtimeVoiceProxy,
    client: RecordingClient,
    brain,
) -> None:
    upstream = RecordingUpstream()
    tasks: set[
        asyncio.Task[Any]
    ] = set()

    await proxy._client_to_openai(
        client,
        upstream,
        brain,
        metadata(),
        "realtime",
        "standard",
        "marin",
        tasks,
        state(),
        provider_epoch=1,
    )

    if tasks:
        await asyncio.gather(
            *tuple(tasks)
        )


@pytest.mark.asyncio
async def test_first_mobile_text_is_durably_accepted(
    tmp_path: Path,
) -> None:
    ledger = RealtimeTurnLedger(
        tmp_path / "turns.db"
    )

    proxy = RealtimeVoiceProxy(
        config(),
        turn_ledger=ledger,
    )

    calls: list[str] = []

    async def brain(
        command: str,
        metadata: dict[str, Any],
        on_delta,
    ):
        calls.append(command)

        return {
            "success": True,
            "response": "Done.",
            "conversation_id":
                "chat-1",
            "intent": "general",
            "model": "test",
        }

    client = RecordingClient(
        [
            text_message(
                "Turn the lights off",
                41,
            )
        ]
    )

    try:
        await run_reader(
            proxy,
            client,
            brain,
        )

        assert calls == [
            "Turn the lights off"
        ]

        accepted = [
            item
            for item in client.messages
            if item.get("type")
            == "turn.accepted"
        ]

        assert len(accepted) == 1
        assert (
            accepted[0][
                "client_turn_id"
            ]
            == 41
        )

        stored = ledger.lookup(
            client_kind="mobile",
            device_id="phone-1",
            conversation_id="chat-1",
            client_turn_id=41,
        )

        assert stored is not None
        # The command was admitted first, then completed durably.
        assert stored.status == "completed"

    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_duplicate_turn_never_runs_brain_twice(
    tmp_path: Path,
) -> None:
    ledger = RealtimeTurnLedger(
        tmp_path / "turns.db"
    )

    proxy = RealtimeVoiceProxy(
        config(),
        turn_ledger=ledger,
    )

    calls: list[str] = []

    async def brain(
        command: str,
        metadata: dict[str, Any],
        on_delta,
    ):
        calls.append(command)

        return {
            "success": True,
            "response": "Done.",
            "conversation_id":
                "chat-1",
            "intent": "general",
            "model": "test",
        }

    first = RecordingClient(
        [
            text_message(
                "Turn the lights off",
                52,
            )
        ]
    )

    duplicate = RecordingClient(
        [
            text_message(
                " Turn   the lights off ",
                52,
            )
        ]
    )

    try:
        await run_reader(
            proxy,
            first,
            brain,
        )

        await run_reader(
            proxy,
            duplicate,
            brain,
        )

        assert calls == [
            "Turn the lights off"
        ]

        statuses = [
            item
            for item
            in duplicate.messages
            if item.get("type")
            == "turn.status"
        ]

        assert len(statuses) == 1
        assert statuses[0]["found"] is True
        assert (
            statuses[0]["status"]
            == "completed"
        )
        assert (
            statuses[0][
                "client_turn_id"
            ]
            == 52
        )

    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_conflicting_reuse_is_rejected(
    tmp_path: Path,
) -> None:
    ledger = RealtimeTurnLedger(
        tmp_path / "turns.db"
    )

    proxy = RealtimeVoiceProxy(
        config(),
        turn_ledger=ledger,
    )

    calls: list[str] = []

    async def brain(
        command: str,
        metadata: dict[str, Any],
        on_delta,
    ):
        calls.append(command)

        return {
            "success": True,
            "response": "Done.",
            "conversation_id":
                "chat-1",
            "intent": "general",
            "model": "test",
        }

    first = RecordingClient(
        [
            text_message(
                "Turn the lights off",
                61,
            )
        ]
    )

    conflict = RecordingClient(
        [
            text_message(
                "Turn the lights on",
                61,
            )
        ]
    )

    try:
        await run_reader(
            proxy,
            first,
            brain,
        )

        await run_reader(
            proxy,
            conflict,
            brain,
        )

        assert calls == [
            "Turn the lights off"
        ]

        conflicts = [
            item
            for item
            in conflict.messages
            if item.get("type")
            == "turn.conflict"
        ]

        assert len(conflicts) == 1
        assert (
            conflicts[0][
                "client_turn_id"
            ]
            == 61
        )

    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_turn_status_survives_new_connection_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "turns.db"

    ledger = RealtimeTurnLedger(path)

    ledger.claim(
        client_kind="mobile",
        device_id="phone-1",
        conversation_id="chat-1",
        client_turn_id=77,
        command="Lock the door",
    )

    ledger.close()

    reopened = RealtimeTurnLedger(
        path
    )

    proxy = RealtimeVoiceProxy(
        config(),
        turn_ledger=reopened,
    )

    async def unused_brain(
        command: str,
        metadata: dict[str, Any],
        on_delta,
    ):
        raise AssertionError(
            "status query must not "
            "start a brain turn"
        )

    client = RecordingClient(
        [
            status_message(77)
        ]
    )

    try:
        await run_reader(
            proxy,
            client,
            unused_brain,
        )

        statuses = [
            item
            for item
            in client.messages
            if item.get("type")
            == "turn.status"
        ]

        assert len(statuses) == 1
        assert statuses[0]["found"] is True
        assert (
            statuses[0]["status"]
            == "accepted"
        )

    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_unknown_turn_status_is_explicit(
    tmp_path: Path,
) -> None:
    ledger = RealtimeTurnLedger(
        tmp_path / "turns.db"
    )

    proxy = RealtimeVoiceProxy(
        config(),
        turn_ledger=ledger,
    )

    async def unused_brain(
        command: str,
        metadata: dict[str, Any],
        on_delta,
    ):
        raise AssertionError(
            "status query must not "
            "start a brain turn"
        )

    client = RecordingClient(
        [
            status_message(999)
        ]
    )

    try:
        await run_reader(
            proxy,
            client,
            unused_brain,
        )

        statuses = [
            item
            for item
            in client.messages
            if item.get("type")
            == "turn.status"
        ]

        assert len(statuses) == 1
        assert statuses[0]["found"] is False
        assert (
            statuses[0]["status"]
            == "unknown"
        )

    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_legacy_zero_turn_id_keeps_existing_behavior(
    tmp_path: Path,
) -> None:
    ledger = RealtimeTurnLedger(
        tmp_path / "turns.db"
    )

    proxy = RealtimeVoiceProxy(
        config(),
        turn_ledger=ledger,
    )

    calls: list[str] = []

    async def brain(
        command: str,
        metadata: dict[str, Any],
        on_delta,
    ):
        calls.append(command)

        return {
            "success": True,
            "response": "Legacy.",
            "conversation_id":
                "chat-1",
            "intent": "general",
            "model": "test",
        }

    client = RecordingClient(
        [
            text_message(
                "Legacy request",
                0,
            )
        ]
    )

    try:
        await run_reader(
            proxy,
            client,
            brain,
        )

        assert calls == [
            "Legacy request"
        ]

        assert not any(
            item.get("type")
            == "turn.accepted"
            for item
            in client.messages
        )

    finally:
        ledger.close()


class TerminalCheckingClient(
    RecordingClient
):
    def __init__(
        self,
        payloads,
        ledger: RealtimeTurnLedger,
        expectations: dict[
            tuple[str, int],
            str,
        ],
    ) -> None:
        super().__init__(payloads)
        self.ledger = ledger
        self.expectations = expectations
        self.checked: set[
            tuple[str, int]
        ] = set()

    async def send_json(
        self,
        payload: dict[str, Any],
    ) -> None:
        event_type = str(
            payload.get("type")
            or ""
        )
        turn_id = int(
            payload.get(
                "client_turn_id"
            )
            or 0
        )

        key = (
            event_type,
            turn_id,
        )

        expected = (
            self.expectations.get(key)
        )

        if expected is not None:
            record = self.ledger.lookup(
                client_kind="mobile",
                device_id="phone-1",
                conversation_id="chat-1",
                client_turn_id=turn_id,
            )

            assert record is not None
            assert record.status == expected

            self.checked.add(key)

        await super().send_json(payload)


@pytest.mark.asyncio
async def test_completed_turn_is_durable_before_terminal_ack(
    tmp_path: Path,
) -> None:
    ledger = RealtimeTurnLedger(
        tmp_path / "turns.db"
    )

    proxy = RealtimeVoiceProxy(
        config(),
        turn_ledger=ledger,
    )

    async def brain(
        command: str,
        metadata: dict[str, Any],
        on_delta,
    ):
        return {
            "success": True,
            "response": "Completed response.",
            "conversation_id": "chat-1",
            "intent": "general",
            "model": "test",
        }

    client = TerminalCheckingClient(
        [
            text_message(
                "Complete this",
                81,
            )
        ],
        ledger,
        {
            (
                "turn.done",
                81,
            ):
                "completed",
        },
    )

    try:
        await run_reader(
            proxy,
            client,
            brain,
        )

        assert (
            "turn.done",
            81,
        ) in client.checked

        record = ledger.lookup(
            client_kind="mobile",
            device_id="phone-1",
            conversation_id="chat-1",
            client_turn_id=81,
        )

        assert record is not None
        assert record.status == "completed"
        assert record.response is not None
        assert (
            record.response["text"]
            == "Completed response."
        )
        assert (
            record.response["success"]
            is True
        )

    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_cancelled_turn_is_durable_before_terminal_ack(
    tmp_path: Path,
) -> None:
    ledger = RealtimeTurnLedger(
        tmp_path / "turns.db"
    )

    proxy = RealtimeVoiceProxy(
        config(),
        turn_ledger=ledger,
    )

    async def brain(
        command: str,
        metadata: dict[str, Any],
        on_delta,
    ):
        await asyncio.Event().wait()

    client = TerminalCheckingClient(
        [
            text_message(
                "Start a long action",
                82,
            ),
            {
                "text": json.dumps(
                    {
                        "type": "cancel",
                        "client_turn_id": 82,
                    }
                )
            },
        ],
        ledger,
        {
            (
                "turn.cancelled",
                82,
            ):
                "cancelled",
        },
    )

    try:
        await run_reader(
            proxy,
            client,
            brain,
        )

        assert (
            "turn.cancelled",
            82,
        ) in client.checked

        record = ledger.lookup(
            client_kind="mobile",
            device_id="phone-1",
            conversation_id="chat-1",
            client_turn_id=82,
        )

        assert record is not None
        assert record.status == "cancelled"

    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_interrupted_turn_is_durable_before_terminal_ack(
    tmp_path: Path,
) -> None:
    ledger = RealtimeTurnLedger(
        tmp_path / "turns.db"
    )

    proxy = RealtimeVoiceProxy(
        config(),
        turn_ledger=ledger,
    )

    release_first = asyncio.Event()

    async def brain(
        command: str,
        metadata: dict[str, Any],
        on_delta,
    ):
        if command == "First request":
            await release_first.wait()

            return {
                "success": True,
                "response": "Old response.",
                "conversation_id": "chat-1",
                "intent": "general",
                "model": "test",
            }

        release_first.set()

        return {
            "success": True,
            "response": "New response.",
            "conversation_id": "chat-1",
            "intent": "general",
            "model": "test",
        }

    client = TerminalCheckingClient(
        [
            text_message(
                "First request",
                91,
            ),
            text_message(
                "Second request",
                92,
            ),
        ],
        ledger,
        {
            (
                "turn.interrupted",
                91,
            ):
                "interrupted",
            (
                "turn.done",
                92,
            ):
                "completed",
        },
    )

    try:
        await run_reader(
            proxy,
            client,
            brain,
        )

        assert (
            "turn.interrupted",
            91,
        ) in client.checked

        assert (
            "turn.done",
            92,
        ) in client.checked

        first = ledger.lookup(
            client_kind="mobile",
            device_id="phone-1",
            conversation_id="chat-1",
            client_turn_id=91,
        )

        second = ledger.lookup(
            client_kind="mobile",
            device_id="phone-1",
            conversation_id="chat-1",
            client_turn_id=92,
        )

        assert first is not None
        assert second is not None

        assert first.status == "interrupted"
        assert second.status == "completed"

    finally:
        ledger.close()
