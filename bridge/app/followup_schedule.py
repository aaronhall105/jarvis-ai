"""Deterministic natural-language schedule resolution for durable follow-ups."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAYPART_HOURS = {"morning": 9, "afternoon": 14, "evening": 19, "tonight": 19}
WEEKDAYS = {name.lower(): index for index, name in enumerate(calendar.day_name)}
MONTHS = {name.lower(): index for index, name in enumerate(calendar.month_name) if name}


@dataclass(frozen=True, slots=True)
class ResolvedSchedule:
    due_utc: datetime
    timezone_name: str
    description: str
    reminder_text: str = "Your requested reminder is due."


@dataclass(frozen=True, slots=True)
class RecurrenceSchedule:
    """A durable recurrence definition independent of the original wording."""

    kind: str
    timezone_name: str
    description: str
    hour: int | None = None
    minute: int = 0
    weekdays: tuple[int, ...] = ()
    day_of_month: int | None = None
    interval_seconds: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "timezone": self.timezone_name,
            "description": self.description,
            "hour": self.hour,
            "minute": self.minute,
            "weekdays": list(self.weekdays),
            "day_of_month": self.day_of_month,
            "interval_seconds": self.interval_seconds,
        }


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
    if len(text) > 5_000:
        return None
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

    relative = re.search(r"\bin\s+(\d{1,5})\s*(seconds?|minutes?|hours?|days?|weeks?)\b", value)
    reminder_text = _reminder_text(value)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        multipliers = {
            "second": 1,
            "minute": 60,
            "hour": 3600,
            "day": 86400,
            "week": 604800,
        }
        seconds = amount * multipliers[unit.rstrip("s")]
        if not 1 <= seconds <= 10 * 366 * 86400:
            return None
        candidate = now + timedelta(seconds=seconds)
        return ResolvedSchedule(
            candidate.astimezone(timezone.utc),
            timezone_name,
            _relative_description(amount, unit),
            reminder_text,
        )

    time_match = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", value)
    daypart_match = re.search(r"\b(morning|afternoon|evening|tonight)\b", value)
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
    if (
        time_match
        and time_match.group(3) is None
        and daypart in {"afternoon", "evening", "tonight"}
        and 1 <= hour <= 11
    ):
        hour += 12

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
    elif "today" in value:
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            return None
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
        reminder_text,
    )


def _relative_description(amount: int, unit: str) -> str:
    rendered_unit = unit.rstrip("s") if amount == 1 else unit.rstrip("s") + "s"
    return f"in {amount} {rendered_unit}"


def _reminder_text(value: str) -> str:
    """Extract reminder content without retaining scheduling words as the message."""

    cleaned = value.strip(" .!?")
    if cleaned.startswith("remind me"):
        cleaned = cleaned[len("remind me") :].strip()
    # The content usually follows ``to`` or ``about``.  Taking the last marker
    # avoids treating ``tomorrow``/``on Friday`` as the reminder itself.
    marker = re.search(r"\b(?:to|about)\s+(.+)$", cleaned)
    if marker:
        content = marker.group(1).strip(" .!?")
        if content:
            return f"Reminder: {content}."
    return "Your requested reminder is due."


def resolve_recurrence(
    text: str,
    *,
    timezone_name: str = "Europe/London",
    now_utc: datetime | None = None,
) -> tuple[RecurrenceSchedule, datetime, str] | None:
    """Resolve supported recurring reminder language to a structured schedule."""

    if len(text) > 5_000:
        return None
    try:
        local_zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return None
    value = " ".join(text.casefold().strip(" .!?").split())
    if not value.startswith("every ") and not value.startswith("remind me every "):
        return None
    explicit_reminder = value.startswith("remind me every ") or " remind me " in value
    if not explicit_reminder:
        return None
    value = value[len("remind me ") :] if value.startswith("remind me ") else value
    reminder_text = _recurring_reminder_text(value)
    now = (now_utc or datetime.now(timezone.utc)).astimezone(local_zone)

    interval = re.match(r"^every\s+(\d{1,4})\s+(hours?|minutes?)\b", value)
    if interval:
        amount = int(interval.group(1))
        multiplier = 3600 if interval.group(2).startswith("hour") else 60
        seconds = amount * multiplier
        if not 60 <= seconds <= 30 * 86400:
            return None
        spec = RecurrenceSchedule(
            kind="interval",
            timezone_name=timezone_name,
            description=f"every {amount} {interval.group(2)}",
            interval_seconds=seconds,
        )
        return spec, (now + timedelta(seconds=seconds)).astimezone(timezone.utc), reminder_text

    time_match = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", value)
    daypart_match = re.search(r"\b(morning|afternoon|evening)\b", value)
    clock = _clock(
        time_match.group(1) if time_match else None,
        time_match.group(2) if time_match else None,
        time_match.group(3) if time_match else None,
        DAYPART_HOURS.get(daypart_match.group(1) if daypart_match else "", 9),
    )
    if clock is None:
        return None
    hour, minute = clock

    if value.startswith("every weekday ") or value.startswith("every weekday at "):
        spec = RecurrenceSchedule(
            kind="weekly",
            timezone_name=timezone_name,
            description=f"every weekday at {hour:02d}:{minute:02d}",
            hour=hour,
            minute=minute,
            weekdays=(0, 1, 2, 3, 4),
        )
    else:
        weekday = next(
            (index for name, index in WEEKDAYS.items() if value.startswith(f"every {name} ")),
            None,
        )
        if weekday is not None:
            spec = RecurrenceSchedule(
                kind="weekly",
                timezone_name=timezone_name,
                description=f"every {calendar.day_name[weekday]} at {hour:02d}:{minute:02d}",
                hour=hour,
                minute=minute,
                weekdays=(weekday,),
            )
        else:
            monthly = re.match(r"^every month on (?:the )?(\d{1,2})(?:st|nd|rd|th)?\b", value)
            if monthly is None:
                return None
            day = int(monthly.group(1))
            if not 1 <= day <= 31:
                return None
            spec = RecurrenceSchedule(
                kind="monthly",
                timezone_name=timezone_name,
                description=f"every month on day {day} at {hour:02d}:{minute:02d}",
                hour=hour,
                minute=minute,
                day_of_month=day,
            )
    next_run = next_recurrence(spec.as_dict(), after_utc=now.astimezone(timezone.utc))
    return (spec, next_run, reminder_text) if next_run is not None else None


def _recurring_reminder_text(value: str) -> str:
    marker = re.search(r"\b(?:to|about)\s+(.+)$", value)
    if marker:
        content = marker.group(1).strip(" .!?")
        if content:
            return f"Recurring reminder: {content}."
    return "Your recurring reminder is due."


def next_recurrence(schedule: Mapping[str, Any], *, after_utc: datetime) -> datetime | None:
    """Calculate the first occurrence strictly after a canonical UTC instant."""

    if after_utc.tzinfo is None:
        after_utc = after_utc.replace(tzinfo=timezone.utc)
    kind = str(schedule.get("kind") or "")
    if kind == "interval":
        seconds = int(schedule.get("interval_seconds") or 0)
        if not 60 <= seconds <= 30 * 86400:
            return None
        return after_utc.astimezone(timezone.utc) + timedelta(seconds=seconds)
    try:
        zone = ZoneInfo(str(schedule.get("timezone") or ""))
    except ZoneInfoNotFoundError:
        return None
    hour_value = schedule.get("hour")
    hour = int(hour_value) if isinstance(hour_value, (str, int)) else 9
    minute = int(schedule.get("minute") or 0)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    local_after = after_utc.astimezone(zone)
    for offset in range(0, 370):
        day = local_after.date() + timedelta(days=offset)
        if kind == "weekly":
            weekdays = {int(item) for item in schedule.get("weekdays") or []}
            if day.weekday() not in weekdays:
                continue
        elif kind == "monthly":
            if day.day != int(schedule.get("day_of_month") or 0):
                continue
        else:
            return None
        candidate = datetime.combine(day, datetime.min.time(), zone).replace(
            hour=hour, minute=minute
        )
        roundtrip = candidate.astimezone(timezone.utc).astimezone(zone)
        if roundtrip.replace(tzinfo=None) != candidate.replace(tzinfo=None):
            continue
        if candidate > local_after:
            return candidate.astimezone(timezone.utc)
    return None


# This parser is deliberately deterministic because it receives raw user text.
def parse_periodic_followup(text: str) -> tuple[str, int] | None:
    """Parse the narrow HA periodic-monitor syntax without regex backtracking."""

    if len(text) > 5_000:
        return None
    words = text.strip().rstrip(".!?").casefold().split()
    prefixes = (("monitor",), ("keep", "checking"), ("keep", "an", "eye", "on"))
    remainder: list[str] | None = None
    for prefix in prefixes:
        if tuple(words[: len(prefix)]) == prefix:
            remainder = words[len(prefix) :]
            break
    if not remainder or len(remainder) not in {1, 4}:
        return None
    entity_id = remainder[0]
    domain, separator, object_id = entity_id.partition(".")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_")
    if (
        separator != "."
        or not domain
        or not object_id
        or len(entity_id) > 255
        or domain[0].isdigit()
        or any(character not in allowed for character in domain + object_id)
    ):
        return None
    interval = 60 * 60
    if len(remainder) == 4:
        every, amount_text, unit = remainder[1:]
        if every != "every" or not amount_text.isascii() or not amount_text.isdigit():
            return None
        amount = int(amount_text)
        if not 1 <= amount <= 999 or unit not in {
            "second",
            "seconds",
            "minute",
            "minutes",
        }:
            return None
        interval = amount * (60 if unit.startswith("minute") else 1)
    return entity_id, interval
