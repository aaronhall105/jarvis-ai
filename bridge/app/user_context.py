from __future__ import annotations

import re
import uuid
from dataclasses import dataclass


def normalise_user_key(user_id: str | None, display_name: str | None) -> str:
    """Return a stable, human-readable user namespace."""

    name = (display_name or "").strip()
    lowered = name.casefold()

    if "amber" in lowered:
        return "amber"
    if "aaron" in lowered:
        return "aaron"

    candidate = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    if candidate:
        return candidate[:64]

    fallback = re.sub(
        r"[^a-z0-9]+",
        "_",
        (user_id or "anonymous").casefold(),
    ).strip("_")
    return (fallback or "anonymous")[:64]


def scope_conversation_id(
    conversation_id: str | None,
    user_key: str,
) -> tuple[str, str]:
    """Return a public conversation ID and a user-isolated storage ID."""

    owner = str(user_key or "").strip()
    if not owner:
        raise ValueError("A user scope is required")
    external_id = str(conversation_id or "").strip() or str(uuid.uuid4())
    if external_id.startswith(f"usr:{owner}:"):
        return external_id, external_id
    return external_id, f"usr:{owner}:{external_id}"


@dataclass(frozen=True, slots=True)
class UserContext:
    """Authenticated Home Assistant user information for one request."""

    user_id: str | None
    user_key: str
    display_name: str
    is_admin: bool
    privilege_verified: bool = False
    device_id: str | None = None
    voice_mode: bool = False
    area_id: str | None = None

    @classmethod
    def from_request(
        cls,
        *,
        user_id: str | None,
        user_name: str | None,
        user_is_admin: bool,
        device_id: str | None,
        voice_mode: bool,
        privilege_verified: bool = False,
        area_id: str | None = None,
    ) -> "UserContext":
        display_name = (user_name or "").strip() or "Aaron"
        return cls(
            user_id=(user_id or "").strip() or None,
            user_key=normalise_user_key(user_id, display_name),
            display_name=display_name,
            is_admin=bool(user_is_admin),
            privilege_verified=bool(privilege_verified),
            device_id=(device_id or "").strip() or None,
            voice_mode=bool(voice_mode),
            area_id=(area_id or "").strip() or None,
        )

    @property
    def can_admin(self) -> bool:
        """Only Aaron's authenticated administrator account may change HA config."""

        return bool(self.user_key == "aaron" and self.is_admin and self.privilege_verified)
