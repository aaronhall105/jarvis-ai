from __future__ import annotations

import re
import time
from typing import Any, Sequence


ROOM_FOLLOW_UP = re.compile(
    r"^\s*(?:in\s+)?what\s+room(?:\s+is\s+"
    r"(?:aaron|amber|he|she|they))?|"
    r"which\s+room(?:\s+is\s+"
    r"(?:aaron|amber|he|she|they)\s+in)?|"
    r"where\s+(?:in\s+the\s+)?(?:flat|house|home)"
    r"(?:\s+is\s+(?:aaron|amber|he|she|they))?"
    r"\s*[?!.]*\s*$",
    re.IGNORECASE,
)

EXPLICIT_PERSON = re.compile(
    r"\b(aaron|amber)\b",
    re.IGNORECASE,
)

PRESENCE_STATEMENT = re.compile(
    r"\b(Aaron|Amber)\s+is\s+"
    r"(?:at\s+home|home|away|not\s+home|at\s+[^.!?;,]+)",
    re.IGNORECASE,
)

PERSON_LOCATION_QUESTION = re.compile(
    r"\bwhere(?:'s|\s+is)\s+(Aaron|Amber)\b",
    re.IGNORECASE,
)


def room_followup_person(
    text: str,
    history: Sequence[dict[str, str]],
) -> str:
    if not ROOM_FOLLOW_UP.match(text or ""):
        return ""

    explicit = EXPLICIT_PERSON.search(text or "")
    if explicit:
        return explicit.group(1).lower()

    for message in reversed(history[-12:]):
        content = str(message.get("content") or "")

        question = PERSON_LOCATION_QUESTION.search(content)
        if question:
            return question.group(1).lower()

        statement = PRESENCE_STATEMENT.search(content)
        if statement:
            return statement.group(1).lower()

    return ""


def household_presence(
    states: Sequence[dict[str, Any]],
) -> dict[str, str]:
    result = {
        "aaron": "unknown",
        "amber": "unknown",
    }

    for entity in states:
        if str(entity.get("domain") or "") != "person":
            continue

        combined = " ".join(
            str(value or "").lower()
            for value in (
                entity.get("entity_id"),
                entity.get("name"),
                entity.get("search_text"),
            )
        )
        state = str(
            entity.get("state") or "unknown"
        ).strip().lower()

        for person in result:
            if person in combined:
                result[person] = state

    return result


def resolve_person_room(
    person: str,
    states: Sequence[dict[str, Any]],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    key = (person or "").strip().lower()
    if key not in {"aaron", "amber"}:
        return {
            "handled": False,
            "response": "",
        }

    display = key.title()
    presence = household_presence(states)
    requested_state = presence.get(key, "unknown")

    if requested_state not in {"home"}:
        if requested_state in {
            "not_home",
            "away",
        }:
            response = (
                f"{display} isn't currently marked as home."
            )
        else:
            response = (
                f"I can't confirm {display}'s current "
                "home status."
            )
        return {
            "handled": True,
            "response": response,
            "person": key,
            "presence": presence,
            "primary_event": None,
        }

    rooms = [
        str(room).strip()
        for room in evidence.get("rooms", [])
        if str(room).strip()
    ]

    unique_rooms: list[str] = []
    for room in rooms:
        if room.casefold() not in {
            item.casefold()
            for item in unique_rooms
        }:
            unique_rooms.append(room)

    source = str(evidence.get("source") or "")
    primary_event = evidence.get("primary_event")
    other = "amber" if key == "aaron" else "aaron"
    other_state = presence.get(other, "unknown")

    if not unique_rooms:
        return {
            "handled": True,
            "response": (
                f"{display} is at home, but the cameras "
                "haven't provided a reliable room match."
            ),
            "person": key,
            "presence": presence,
            "primary_event": primary_event,
        }

    if len(unique_rooms) > 1:
        joined = ", ".join(unique_rooms)
        return {
            "handled": True,
            "response": (
                "The cameras currently show people in "
                f"{joined}, so I can't safely determine "
                f"which room {display} is in."
            ),
            "person": key,
            "presence": presence,
            "primary_event": primary_event,
        }

    room = unique_rooms[0]
    evidence_phrase = (
        "a live camera check"
        if source == "live_snapshots"
        else "the latest camera detection"
    )

    if other_state not in {"home"}:
        response = (
            f"{display} appears to be in the {room}, "
            f"based on {evidence_phrase}."
        )
    else:
        response = (
            f"The cameras show someone in the {room}, "
            f"but I can't safely tell whether that's "
            f"{display}."
        )

    return {
        "handled": True,
        "response": response,
        "person": key,
        "presence": presence,
        "primary_event": primary_event,
    }


def recent_person_rooms(
    events: Sequence[dict[str, Any]],
    *,
    now: float | None = None,
    maximum_age_seconds: int = 180,
) -> dict[str, Any]:
    current = time.time() if now is None else now
    matching = []

    for event in events:
        if str(event.get("label") or "").lower() != "person":
            continue

        started = float(event.get("start_time") or 0.0)
        if started <= 0.0:
            continue
        if current - started > maximum_age_seconds:
            continue

        area = str(event.get("area") or "").strip()
        if not area:
            continue
        if area.casefold() in {
            "front door",
            "outside",
        }:
            continue

        matching.append(event)

    matching.sort(
        key=lambda event: float(
            event.get("start_time") or 0.0
        ),
        reverse=True,
    )

    rooms = []
    for event in matching:
        area = str(event.get("area") or "").strip()
        if area and area not in rooms:
            rooms.append(area)

    return {
        "source": "recent_events",
        "rooms": rooms,
        "events": matching,
        "primary_event": (
            matching[0] if matching else None
        ),
    }
