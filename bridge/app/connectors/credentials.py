"""Credential resolution and defensive redaction for connector boundaries."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REDACTED = "[REDACTED]"
_MAX_SECRET_BYTES = 64 * 1024
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")

_SENSITIVE_KEY_PARTS = {
    "apikey",
    "accesstoken",
    "refreshtoken",
    "authtoken",
    "authenticationtoken",
    "authorization",
    "bearertoken",
    "clientsecret",
    "cookie",
    "credential",
    "credentials",
    "password",
    "passwd",
    "privatekey",
    "secret",
    "secretkey",
    "session",
    "sessionid",
    "token",
}

_PEM_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
    r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_AUTH_HEADER = re.compile(
    r"(?i)(\b(?:authorization|proxy-authorization)\s*:\s*)"
    r"(?:bearer|basic)\s+[^\s,;]+"
)
_COOKIE_HEADER = re.compile(r"(?im)(\b(?:set-cookie|cookie)\s*:\s*)[^\r\n]+")
_URL_CREDENTIAL = re.compile(
    r"(?i)([?&](?:access_token|refresh_token|api_key|apikey|token|secret|password)=)"
    r"[^&#\s]+"
)
_KEY_VALUE = re.compile(
    r"(?i)(\b(?:api[ _-]?key|access[ _-]?token|refresh[ _-]?token|auth[ _-]?token|"
    r"client[ _-]?secret|secret[ _-]?key|password|passwd|private[ _-]?key|session[ _-]?id|"
    r"credential|token)\b[\"']?\s*(?:=|:|\bis\b)\s*)"
    r"([\"']?)[^\s,;&}\"']+\2"
)
_BEARER_VALUE = re.compile(r"(?i)(\b(?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_SERVICE_KEY = re.compile(
    r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|AKIA[A-Z0-9]{12,}|"
    r"gh[opusr]_[A-Za-z0-9_]{20,})\b"
)


class CredentialError(RuntimeError):
    """A credential source was configured but could not be safely resolved."""


class SecretValue:
    """Small SecretStr-like wrapper whose normal rendering never reveals data."""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError("secret value must be a non-empty string")
        self.__value = value

    def reveal(self) -> str:
        """Explicitly obtain the value at the provider call boundary."""

        return self.__value

    def __bool__(self) -> bool:
        return bool(self.__value)

    def __repr__(self) -> str:
        return "SecretValue('[REDACTED]')"

    def __str__(self) -> str:
        return REDACTED


@dataclass(frozen=True, slots=True)
class ResolvedCredential:
    name: str
    value: SecretValue
    source: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "configured": True, "source": self.source}


class CredentialResolver:
    """Resolve secrets from process environment or read-only mounted files.

    For ``JARVIS_GMAIL_TOKEN``, an explicit ``JARVIS_GMAIL_TOKEN_FILE`` is
    checked first, then the direct environment value, then a mounted file named
    ``jarvis_gmail_token`` below ``secret_root``.  File paths and values never
    appear in errors or status output.
    """

    def __init__(
        self,
        *,
        environment: Mapping[str, str] | None = None,
        secret_root: str | Path = "/run/secrets",
    ) -> None:
        self._environment = environment if environment is not None else os.environ
        self._secret_root = Path(secret_root)

    @staticmethod
    def _validate_name(env_name: str) -> str:
        name = str(env_name or "").strip().upper()
        if not _ENV_NAME.fullmatch(name):
            raise ValueError("credential environment name is invalid")
        return name

    @staticmethod
    def _read_file(path: Path, name: str) -> str:
        try:
            if not path.is_file():
                raise CredentialError(f"Credential file for {name} is not a regular file")
            size = path.stat().st_size
            if size <= 0 or size > _MAX_SECRET_BYTES:
                raise CredentialError(f"Credential file for {name} has an invalid size")
            raw = path.read_bytes()
            value = raw.decode("utf-8").rstrip("\r\n")
        except CredentialError:
            raise
        except (OSError, UnicodeError) as exc:
            raise CredentialError(f"Credential file for {name} could not be read") from exc
        if not value or "\x00" in value:
            raise CredentialError(f"Credential file for {name} is empty or invalid")
        return value

    def resolve(
        self,
        env_name: str,
        *,
        mounted_name: str | None = None,
    ) -> ResolvedCredential | None:
        name = self._validate_name(env_name)
        file_env = str(self._environment.get(f"{name}_FILE") or "").strip()
        if file_env:
            value = self._read_file(Path(file_env), name)
            return ResolvedCredential(name, SecretValue(value), "mounted_secret")

        direct = self._environment.get(name)
        if direct:
            return ResolvedCredential(name, SecretValue(str(direct)), "environment")

        filename = mounted_name or name.casefold()
        if Path(filename).name != filename:
            raise ValueError("mounted credential name must be a filename")
        mounted = self._secret_root / filename
        if mounted.is_file():
            value = self._read_file(mounted, name)
            return ResolvedCredential(name, SecretValue(value), "mounted_secret")
        return None

    def resolve_provider(self, provider_id: str, key: str) -> ResolvedCredential | None:
        provider = re.sub(r"[^A-Za-z0-9]+", "_", str(provider_id)).strip("_").upper()
        setting = re.sub(r"[^A-Za-z0-9]+", "_", str(key)).strip("_").upper()
        if not provider or not setting:
            raise ValueError("provider_id and key must not be empty")
        return self.resolve(f"JARVIS_{provider}_{setting}")


def _sensitive_key(key: Any) -> bool:
    rendered = re.sub(r"[^a-z0-9]", "", str(key).casefold())
    return rendered in _SENSITIVE_KEY_PARTS or any(
        rendered.endswith(part) for part in _SENSITIVE_KEY_PARTS if len(part) >= 6
    )


def redact_text(
    text: Any,
    *,
    known_secrets: Sequence[str] = (),
    max_length: int = 8_000,
) -> str:
    """Redact credentials embedded in otherwise ordinary free text."""

    rendered = str(text or "")
    for secret in sorted({str(item) for item in known_secrets if str(item)}, key=len, reverse=True):
        rendered = rendered.replace(secret, REDACTED)
    rendered = _PEM_PRIVATE_KEY.sub(REDACTED, rendered)
    rendered = _AUTH_HEADER.sub(lambda match: f"{match.group(1)}{REDACTED}", rendered)
    rendered = _COOKIE_HEADER.sub(lambda match: f"{match.group(1)}{REDACTED}", rendered)
    rendered = _URL_CREDENTIAL.sub(lambda match: f"{match.group(1)}{REDACTED}", rendered)
    rendered = _KEY_VALUE.sub(lambda match: f"{match.group(1)}{REDACTED}", rendered)
    rendered = _BEARER_VALUE.sub(lambda match: f"{match.group(1)}{REDACTED}", rendered)
    rendered = _JWT.sub(REDACTED, rendered)
    rendered = _SERVICE_KEY.sub(REDACTED, rendered)
    if len(rendered) > max_length:
        rendered = f"{rendered[:max_length]}...[TRUNCATED]"
    return rendered


def redact_secrets(
    value: Any,
    *,
    known_secrets: Sequence[str] = (),
    max_depth: int = 12,
    max_items: int = 200,
    max_string: int = 8_000,
) -> Any:
    """Recursively sanitize mappings, containers, exceptions, and free text."""

    seen: set[int] = set()

    def visit(item: Any, depth: int) -> Any:
        if depth > max_depth:
            return "[MAX_DEPTH]"
        if item is None or isinstance(item, (bool, int, float)):
            return item
        if isinstance(item, (bytes, bytearray, memoryview, SecretValue)):
            return REDACTED
        if isinstance(item, str):
            return redact_text(item, known_secrets=known_secrets, max_length=max_string)
        if isinstance(item, BaseException):
            return redact_text(str(item), known_secrets=known_secrets, max_length=max_string)

        identity = id(item)
        if identity in seen:
            return "[CYCLE]"
        seen.add(identity)
        try:
            if isinstance(item, Mapping):
                output: dict[Any, Any] = {}
                for index, (key, child) in enumerate(item.items()):
                    if index >= max_items:
                        output["[TRUNCATED]"] = True
                        break
                    output[key] = REDACTED if _sensitive_key(key) else visit(child, depth + 1)
                return output
            if isinstance(item, tuple):
                return tuple(visit(child, depth + 1) for child in item[:max_items])
            if isinstance(item, (list, set, frozenset)):
                children = list(item)[:max_items]
                return [visit(child, depth + 1) for child in children]
            return redact_text(item, known_secrets=known_secrets, max_length=max_string)
        finally:
            seen.discard(identity)

    return visit(value, 0)
