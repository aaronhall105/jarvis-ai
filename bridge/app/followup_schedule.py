"""Deterministic natural-language schedule resolution for durable follow-ups."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAYPART_HOURS = {"morning": 9, "afternoon": 14, "evening": 19}
WEEKDAYS = {name.lower(): index for index, name in enumerate(calendar.day_name)}
MONTHS = {name.lower(): index for index, name in enumerate(calendar.month_name) if name}


@dataclass(frozen=True, slots=True)
class ResolvedSchedule:
    due_utc: datetime
    timezone_name: str
    description: str


def _clock(
    hour_text: str | None, minute_text: str | None, meridiem: str | None, default_hour: int
) -> tuple[int, int] | None:
    if hour_text is None:
        return default_hour, 0
    hour, minute = int(hour_text), int(minute_text or 0)
    if minute > 59 or hour > (12 if meridiem else 23) or hour < 0 or (meridiem and hour < 1):
        return None
    if meridiem:
        hour = (hour % 12) + (12 if meridiem.lower() == "pm" else 0)
    return hour, minute


def resolve_schedule(
    text: str, *, timezone_name: str = "Europe/London", now_utc: datetime | None = None
) -> ResolvedSchedule | None:
    """Resolve supported reminder language to one future canonical UTC instant."""
    try:
        local_zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return None
    now = (now_utc or datetime.now(timezone.utc)).astimezone(local_zone)
    value = " ".join(text.casefold().strip(" .!?").split())
    if not re.search(
        r"\b(?:remind me|tell me|let me know|check(?: this)? again|check next)\b", value
    ):
        return None

    time_match = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", value)
    daypart_match = re.search(r"\b(morning|afternoon|evening)\b", value)
    daypart = daypart_match.group(1) if daypart_match else None
    clock = _clock(
        time_match.group(1) if time_match else None,
        time_match.group(2) if time_match else None,
        time_match.group(3) if time_match else None,
        DAYPART_HOURS.get(daypart or "", 9),
    )
    if clock is None:
        return None
    hour, minute = clock

    target_date = None
    absolute = re.search(r"\b(?:on\s+)?(\d{1,2})\s+([a-z]+)(?:\s+(\d{4}))?\b", value)
    if absolute and absolute.group(2) in MONTHS:
        year = int(absolute.group(3) or now.year)
        try:
            target_date = now.date().replace(
                year=year, month=MONTHS[absolute.group(2)], day=int(absolute.group(1))
            )
        except ValueError:
            return None
        candidate = datetime.combine(target_date, datetime.min.time(), local_zone).replace(
            hour=hour, minute=minute
        )
        if candidate <= now and absolute.group(3) is None:
            try:
                candidate = candidate.replace(year=year + 1)
            except ValueError:
                return None
    elif "tomorrow" in value:
        candidate = (now + timedelta(days=1)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
    else:
        weekday = next(
            (index for name, index in WEEKDAYS.items() if re.search(rf"\b{name}\b", value)), None
        )
        if weekday is not None:
            days = (weekday - now.weekday()) % 7
            if days == 0:
                days = 7
            candidate = (now + timedelta(days=days)).replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
        elif time_match:
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
        else:
            return None

    # Reject nonexistent wall times across DST jumps. Ambiguous fall-back times
    # deliberately use fold=0, matching the project's recurring scheduler.
    roundtrip = candidate.astimezone(timezone.utc).astimezone(local_zone)
    if roundtrip.replace(tzinfo=None) != candidate.replace(tzinfo=None):
        return None
    if candidate <= now or candidate > now + timedelta(days=3660):
        return None
    return ResolvedSchedule(
        candidate.astimezone(timezone.utc),
        timezone_name,
        candidate.strftime("%A %d %B at %H:%M %Z"),
    )
