"""Real web search and safe page retrieval for Jarvis.

A search is successful only when the provider reports a completed web-search
call and supplies source URLs. Page retrieval rejects local/private targets so
model-selected URLs cannot turn Core into an SSRF proxy.
"""

from __future__ import annotations

import asyncio
import codecs
import inspect
import io
import ipaddress
import json
import re
import socket
import ssl
import time
import weakref
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
import httpcore
from openai import AsyncOpenAI


_TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src"}
_SPACE = re.compile(r"\s+")
_DEFAULT_DNS_TIMEOUT_SECONDS = 5.0
_MAX_CONCURRENT_DNS_RESOLUTIONS = 8
_MAX_RESOLVED_ADDRESSES = 8
_DNS_RESOLUTION_SLOTS: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop,
    asyncio.Semaphore,
] = weakref.WeakKeyDictionary()
_DATE_META_NAMES = {
    "article:published_time": "published_at",
    "date": "published_at",
    "datepublished": "published_at",
    "article:modified_time": "updated_at",
    "datemodified": "updated_at",
    "last-modified": "updated_at",
}


class PublicURLRejectedError(ValueError):
    """The requested URL is syntactically invalid or targets a non-public host."""


class PublicURLResolutionError(RuntimeError):
    """A public URL hostname could not be resolved within the safety budget."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonicalize_url(value: str) -> str:
    """Return a stable HTTP(S) URL without common tracking fields."""

    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute HTTP(S) URLs are supported")
    if parsed.username or parsed.password:
        raise ValueError("Credential-bearing URLs are not supported")
    hostname = parsed.hostname.rstrip(".").lower()
    port = parsed.port
    default_port = (parsed.scheme.lower() == "http" and port == 80) or (
        parsed.scheme.lower() == "https" and port == 443
    )
    bracketed_hostname = f"[{hostname}]" if ":" in hostname else hostname
    authority = (
        bracketed_hostname if port is None or default_port else f"{bracketed_hostname}:{port}"
    )
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_KEYS
    ]
    return urlunsplit(
        (
            parsed.scheme.lower(),
            authority,
            parsed.path or "/",
            urlencode(query, doseq=True),
            "",
        )
    )


def _is_globally_routable(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return whether an address is safe for untrusted public-web retrieval."""

    if isinstance(address, ipaddress.IPv6Address):
        if address.scope_id is not None or address.is_site_local:
            return False
        mapped = address.ipv4_mapped
        if mapped is not None:
            return _is_globally_routable(mapped)
    return bool(address.is_global and not address.is_multicast)


def _dns_resolution_slots() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    slots = _DNS_RESOLUTION_SLOTS.get(loop)
    if slots is None:
        slots = asyncio.Semaphore(_MAX_CONCURRENT_DNS_RESOLUTIONS)
        _DNS_RESOLUTION_SLOTS[loop] = slots
    return slots


async def _bounded_getaddrinfo(
    hostname: str,
    *,
    timeout_seconds: float,
) -> list[tuple[Any, ...]]:
    """Bound resolver admission and retain its slot until its worker exits."""

    deadline = time.monotonic() + timeout_seconds
    slots = _dns_resolution_slots()
    await asyncio.wait_for(slots.acquire(), timeout=timeout_seconds)
    try:
        resolver = asyncio.create_task(
            asyncio.to_thread(
                socket.getaddrinfo,
                hostname,
                None,
                type=socket.SOCK_STREAM,
            )
        )
    except BaseException:
        slots.release()
        raise

    def release_slot(task: asyncio.Task[list[tuple[Any, ...]]]) -> None:
        slots.release()
        if task.cancelled():
            return
        task.exception()

    resolver.add_done_callback(release_slot)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    return await asyncio.wait_for(asyncio.shield(resolver), timeout=remaining)


async def _resolve_public_addresses(
    hostname: str,
    *,
    timeout_seconds: float = _DEFAULT_DNS_TIMEOUT_SECONDS,
    max_addresses: int = _MAX_RESOLVED_ADDRESSES,
) -> tuple[str, ...]:
    """Resolve a host and return only an all-public, connectable address set."""

    safe_timeout = max(0.05, min(float(timeout_seconds), 30.0))
    safe_max_addresses = max(1, min(int(max_addresses), 16))
    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        try:
            info = await _bounded_getaddrinfo(
                hostname,
                timeout_seconds=safe_timeout,
            )
        except TimeoutError as exc:
            raise PublicURLResolutionError("URL host resolution timed out") from exc
        except (OSError, UnicodeError) as exc:
            raise PublicURLResolutionError("URL host could not be resolved") from exc
        addresses = []
        for entry in info:
            try:
                addresses.append(ipaddress.ip_address(entry[4][0]))
            except ValueError:
                continue
    if not addresses:
        raise PublicURLResolutionError("URL host did not resolve to an IP address")
    if any(not _is_globally_routable(address) for address in addresses):
        raise PublicURLRejectedError("Local, private, or non-global network URLs are not allowed")
    unique = tuple(dict.fromkeys(str(address) for address in addresses))
    return unique[:safe_max_addresses]


async def assert_public_url(
    value: str,
    *,
    timeout_seconds: float = _DEFAULT_DNS_TIMEOUT_SECONDS,
    max_addresses: int = _MAX_RESOLVED_ADDRESSES,
) -> str:
    """Resolve a URL and reject non-public destinations before retrieval."""

    canonical = canonicalize_url(value)
    hostname = urlsplit(canonical).hostname
    if hostname is None:
        raise ValueError("URL does not contain a host")
    await _resolve_public_addresses(
        hostname,
        timeout_seconds=timeout_seconds,
        max_addresses=max_addresses,
    )
    return canonical


class _PinnedPublicNetworkBackend(httpcore.AsyncNetworkBackend):
    """Resolve and validate at connect time, then connect to that exact IP.

    The HTTP/TLS layer retains the original hostname for Host and SNI. This
    closes the gap where a hostname could resolve publicly during validation
    and privately when the HTTP client's socket was opened.
    """

    def __init__(
        self,
        *,
        resolution_timeout_seconds: float = _DEFAULT_DNS_TIMEOUT_SECONDS,
        max_addresses: int = _MAX_RESOLVED_ADDRESSES,
        connect_budget_seconds: float = 8.0,
    ) -> None:
        self._backend = httpcore.AnyIOBackend()
        self._resolution_timeout_seconds = max(
            0.05,
            min(float(resolution_timeout_seconds), 30.0),
        )
        self._max_addresses = max(1, min(int(max_addresses), 16))
        self._connect_budget_seconds = max(
            0.05,
            min(float(connect_budget_seconds), 30.0),
        )

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> Any:
        budget = self._connect_budget_seconds
        if timeout is not None:
            budget = min(budget, max(0.0, float(timeout)))
        if budget <= 0:
            raise httpcore.ConnectTimeout("Public host connect budget expired")
        deadline = time.monotonic() + budget
        try:
            addresses = await _resolve_public_addresses(
                host,
                timeout_seconds=min(
                    self._resolution_timeout_seconds,
                    max(0.05, deadline - time.monotonic()),
                ),
                max_addresses=self._max_addresses,
            )
        except PublicURLResolutionError as exc:
            if time.monotonic() >= deadline:
                raise httpcore.ConnectTimeout(
                    "Public host resolution exceeded the connect budget"
                ) from exc
            raise httpcore.ConnectError(str(exc)) from exc
        last_error: Exception | None = None
        for address in addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise httpcore.ConnectTimeout("Public host connect budget expired") from last_error
            try:
                return await asyncio.wait_for(
                    self._backend.connect_tcp(
                        address,
                        port,
                        timeout=remaining,
                        local_address=local_address,
                        socket_options=socket_options,
                    ),
                    timeout=remaining,
                )
            except TimeoutError as exc:
                raise httpcore.ConnectTimeout("Public host connect budget expired") from exc
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("Public host did not resolve to a connectable address")

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Any = None,
    ) -> Any:
        del path, timeout, socket_options
        raise RuntimeError("Unix sockets are not supported by public web fetch")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class _PinnedResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream: Any) -> None:
        self._stream = stream

    async def __aiter__(self):
        async for chunk in self._stream:
            yield chunk

    async def aclose(self) -> None:
        closer = getattr(self._stream, "aclose", None)
        if callable(closer):
            await closer()


class _PinnedPublicTransport(httpx.AsyncBaseTransport):
    """Small httpx/httpcore adapter using the connect-time public resolver."""

    def __init__(
        self,
        *,
        resolution_timeout_seconds: float = _DEFAULT_DNS_TIMEOUT_SECONDS,
        max_addresses: int = _MAX_RESOLVED_ADDRESSES,
        connect_budget_seconds: float = 8.0,
    ) -> None:
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            max_connections=20,
            max_keepalive_connections=10,
            keepalive_expiry=30.0,
            network_backend=_PinnedPublicNetworkBackend(
                resolution_timeout_seconds=resolution_timeout_seconds,
                max_addresses=max_addresses,
                connect_budget_seconds=connect_budget_seconds,
            ),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not isinstance(request.stream, httpx.AsyncByteStream):
            raise TypeError("Public web fetch requires an asynchronous request stream")
        response = await self._pool.handle_async_request(
            httpcore.Request(
                method=request.method,
                url=httpcore.URL(
                    scheme=request.url.raw_scheme,
                    host=request.url.raw_host,
                    port=request.url.port,
                    target=request.url.raw_path,
                ),
                headers=request.headers.raw,
                content=request.stream,
                extensions=request.extensions,
            )
        )
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_PinnedResponseStream(response.stream),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


@dataclass(frozen=True, slots=True)
class WebSource:
    title: str
    url: str
    canonical_url: str
    provider: str
    retrieved_at: str
    published_at: str | None = None
    updated_at: str | None = None
    snippet: str = ""
    source_type: str = "web"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WebSearchEvidence:
    query: str
    answer: str
    sources: tuple[WebSource, ...]
    provider: str
    provider_reference: str | None
    retrieved_at: str
    searched: bool

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["sources"] = [source.as_dict() for source in self.sources]
        return payload


@dataclass(frozen=True, slots=True)
class FetchedPage:
    url: str
    canonical_url: str
    title: str
    text: str
    content_type: str
    status_code: int
    retrieved_at: str
    published_at: str | None = None
    updated_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    dumper = getattr(value, "model_dump", None)
    if callable(dumper):
        result = dumper(mode="json")
        if isinstance(result, Mapping):
            return result
    return {}


def _sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value
    return ()


class OpenAIWebSearchClient:
    """Provider-backed current web evidence through the Responses API."""

    provider_id = "openai_web_search"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        enabled: bool = True,
        timeout_seconds: float = 45.0,
        health_cache_seconds: float = 900.0,
        client: Any | None = None,
    ) -> None:
        self.configured = bool(enabled and api_key.strip() and model.strip())
        self.model = model.strip()
        self.health_cache_seconds = max(
            30.0,
            min(float(health_cache_seconds), 3600.0),
        )
        self._last_success_at = 0.0
        self._owns_client = client is None and self.configured
        self._closed = False
        if client is not None:
            self._client: Any | None = client
        elif self.configured:
            self._client = AsyncOpenAI(
                api_key=api_key,
                max_retries=1,
                timeout=httpx.Timeout(
                    timeout_seconds,
                    connect=min(timeout_seconds, 8.0),
                    pool=min(timeout_seconds, 8.0),
                ),
            )
        else:
            self._client = None

    async def aclose(self) -> None:
        """Close the provider client when this wrapper created it."""

        if self._closed:
            return
        self._closed = True
        if not self._owns_client or self._client is None:
            return
        closer = getattr(self._client, "close", None)
        if not callable(closer):
            return
        outcome = closer()
        if inspect.isawaitable(outcome):
            await outcome

    def invalidate_health_cache(self) -> None:
        self._last_success_at = 0.0

    async def health(self) -> dict[str, Any]:
        if not self.configured:
            return {
                "configured": False,
                "authenticated": False,
                "healthy": False,
                "reason": "OpenAI web search is disabled or missing model credentials.",
            }
        if (
            self.health_cache_seconds > 0
            and self._last_success_at
            and time.monotonic() - self._last_success_at <= self.health_cache_seconds
        ):
            return {
                "configured": True,
                "authenticated": True,
                "healthy": True,
                "reason": None,
            }
        try:
            evidence = await self.search(
                "OpenAI official website",
                limit=1,
                max_tool_calls=1,
            )
        except Exception as exc:
            authentication_failed = type(exc).__name__ == "AuthenticationError"
            return {
                "configured": True,
                "authenticated": not authentication_failed,
                "healthy": False,
                "reason": f"Web search probe failed ({type(exc).__name__}).",
            }
        return {
            "configured": True,
            "authenticated": True,
            "healthy": bool(evidence.sources),
            "reason": None if evidence.sources else "The provider returned no web evidence.",
        }

    async def search(
        self,
        query: str,
        *,
        limit: int = 8,
        max_tool_calls: int = 3,
    ) -> WebSearchEvidence:
        clean_query = _SPACE.sub(" ", str(query or "")).strip()
        if not self.configured:
            raise RuntimeError("OpenAI web search is not configured")
        if not clean_query:
            raise ValueError("Search query cannot be empty")
        if self._client is None:  # pragma: no cover - guarded by configured
            raise RuntimeError("OpenAI web search client is unavailable")
        safe_limit = max(1, min(int(limit), 20))
        response = await self._client.responses.create(
            model=self.model,
            tools=[{"type": "web_search", "external_web_access": True}],
            tool_choice="required",
            include=["web_search_call.action.sources"],
            max_tool_calls=max(1, min(int(max_tool_calls), 8)),
            max_output_tokens=900,
            store=False,
            instructions=(
                "Search the live web for the user's query. Give a concise factual "
                "evidence summary. Never answer solely from model memory."
            ),
            input=clean_query,
        )
        payload = _as_mapping(response)
        output = _sequence(payload.get("output"))
        searched = False
        source_rows: list[Mapping[str, Any]] = []
        citations: list[Mapping[str, Any]] = []
        for raw_item in output:
            item = _as_mapping(raw_item)
            if item.get("type") == "web_search_call":
                searched = item.get("status") == "completed"
                action = _as_mapping(item.get("action"))
                source_rows.extend(
                    _as_mapping(source) for source in _sequence(action.get("sources"))
                )
            if item.get("type") == "message":
                for raw_content in _sequence(item.get("content")):
                    content = _as_mapping(raw_content)
                    citations.extend(
                        _as_mapping(annotation)
                        for annotation in _sequence(content.get("annotations"))
                        if _as_mapping(annotation).get("type") == "url_citation"
                    )
        source_rows.extend(citations)
        retrieved_at = _utc_now()
        sources: list[WebSource] = []
        seen: set[str] = set()
        for row in source_rows:
            raw_url = str(row.get("url") or "").strip()
            if not raw_url:
                continue
            try:
                canonical = canonicalize_url(raw_url)
            except (TypeError, ValueError):
                continue
            if canonical in seen:
                continue
            seen.add(canonical)
            sources.append(
                WebSource(
                    title=_SPACE.sub(" ", str(row.get("title") or canonical)).strip()[:500],
                    url=raw_url,
                    canonical_url=canonical,
                    provider=self.provider_id,
                    retrieved_at=retrieved_at,
                    source_type=str(row.get("type") or "web")[:100],
                )
            )
            if len(sources) >= safe_limit:
                break
        answer = str(
            payload.get("output_text") or getattr(response, "output_text", "") or ""
        ).strip()
        if not searched or not sources:
            raise RuntimeError("The web provider did not return verifiable source evidence")
        self._last_success_at = time.monotonic()
        return WebSearchEvidence(
            query=clean_query,
            answer=answer,
            sources=tuple(sources),
            provider=self.provider_id,
            provider_reference=str(payload.get("id") or "") or None,
            retrieved_at=retrieved_at,
            searched=True,
        )

    async def analyze_conflicts(
        self,
        question: str,
        sources: Sequence[Mapping[str, Any]],
    ) -> tuple[Mapping[str, Any], ...]:
        """Compare only supplied live evidence and return structured conflicts."""

        if not self.configured:
            raise RuntimeError("OpenAI research analysis is not configured")
        clean_question = _SPACE.sub(" ", str(question or "")).strip()
        if not clean_question:
            raise ValueError("Research question cannot be empty")
        if self._client is None:  # pragma: no cover - guarded by configured
            raise RuntimeError("OpenAI research client is unavailable")
        evidence: list[dict[str, str]] = []
        remaining = 36_000
        for index, raw in enumerate(sources[:10], start=1):
            canonical_url = str(raw.get("canonical_url") or raw.get("url") or "").strip()
            title = _SPACE.sub(" ", str(raw.get("title") or canonical_url)).strip()
            body = str(raw.get("text") or raw.get("snippet") or "").strip()
            if not canonical_url or not body or remaining <= 0:
                continue
            excerpt = body[: min(5_000, remaining)]
            remaining -= len(excerpt)
            evidence.append(
                {
                    "source_id": f"S{index}",
                    "title": title[:500],
                    "canonical_url": canonical_url,
                    "evidence_text": excerpt,
                }
            )
        if len(evidence) < 2:
            return ()
        schema = {
            "type": "object",
            "properties": {
                "conflicts": {
                    "type": "array",
                    "maxItems": 20,
                    "items": {
                        "type": "object",
                        "properties": {
                            "topic": {"type": "string"},
                            "values": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 2,
                                "maxItems": 10,
                            },
                            "source_urls": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 2,
                                "maxItems": 10,
                            },
                            "description": {"type": "string"},
                        },
                        "required": [
                            "topic",
                            "values",
                            "source_urls",
                            "description",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["conflicts"],
            "additionalProperties": False,
        }
        response = await self._client.responses.create(
            model=self.model,
            instructions=(
                "Compare only the supplied source excerpts for the research "
                "question. Report a conflict only when two or more supplied "
                "sources explicitly state incompatible values. Copy each value "
                "verbatim from an excerpt and use only supplied canonical URLs. "
                "Return an empty conflicts array when evidence is insufficient. "
                "Do not add facts from model memory."
            ),
            input=json.dumps(
                {"question": clean_question, "sources": evidence},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "jarvis_source_conflicts",
                    "strict": True,
                    "schema": schema,
                }
            },
            max_output_tokens=1_600,
            store=False,
        )
        payload = _as_mapping(response)
        status = str(payload.get("status") or "completed")
        if status != "completed":
            raise RuntimeError("Research conflict analysis did not complete")
        output_text = str(
            payload.get("output_text") or getattr(response, "output_text", "") or ""
        ).strip()
        try:
            parsed = json.loads(output_text)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "Research conflict analysis returned malformed structured output"
            ) from exc
        rows = parsed.get("conflicts") if isinstance(parsed, Mapping) else None
        if not isinstance(rows, list):
            raise RuntimeError("Research conflict analysis omitted conflicts")
        return tuple(item for item in rows if isinstance(item, Mapping))


class _BoundedTextAccumulator:
    def __init__(self, limit: int, *, strip_leading: bool = False) -> None:
        self._limit = max(1, int(limit))
        self._strip_leading = strip_leading
        self._buffer = io.StringIO()
        self._length = 0

    def append(self, value: str, *, separator: str = "") -> None:
        if self._length >= self._limit:
            return
        if self._strip_leading and self._length == 0:
            value = value.lstrip()
        if not value:
            return
        remaining = self._limit - self._length
        if self._length and separator:
            addition = separator[:remaining]
            self._buffer.write(addition)
            self._length += len(addition)
            remaining = self._limit - self._length
        if remaining:
            addition = value[:remaining]
            self._buffer.write(addition)
            self._length += len(addition)

    @property
    def value(self) -> str:
        return self._buffer.getvalue().strip()


class _TextExtractor(HTMLParser):
    def __init__(self, *, max_text_characters: int) -> None:
        super().__init__(convert_charrefs=True)
        self._title = _BoundedTextAccumulator(500)
        self._text = _BoundedTextAccumulator(max_text_characters)
        self.canonical: str | None = None
        self.published_at: str | None = None
        self.updated_at: str | None = None
        self._title_depth = 0
        self._ignored_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        lowered = tag.lower()
        attributes = {str(key).lower(): str(value or "")[:4_000] for key, value in attrs}
        if lowered in {"script", "style", "noscript", "svg", "template"}:
            self._ignored_depth += 1
        if lowered == "title":
            self._title_depth += 1
        if lowered == "link" and attributes.get("rel", "").lower() == "canonical":
            self.canonical = attributes.get("href") or self.canonical
        if lowered == "meta":
            name = (attributes.get("property") or attributes.get("name") or "").lower()
            destination = _DATE_META_NAMES.get(name)
            content = attributes.get("content", "").strip()
            if destination and content and getattr(self, destination) is None:
                setattr(self, destination, content[:200])

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "title" and self._title_depth:
            self._title_depth -= 1
        if lowered in {"script", "style", "noscript", "svg", "template"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        clean = _SPACE.sub(" ", data).strip()
        if not clean:
            return
        if self._title_depth:
            self._title.append(clean, separator=" ")
        if not self._ignored_depth:
            self._text.append(clean, separator="\n")

    @property
    def title(self) -> str:
        return self._title.value

    @property
    def text(self) -> str:
        return self._text.value


class SafeWebFetcher:
    """Pooled, bounded HTTP page retrieval with checked redirects."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        max_bytes: int = 2_000_000,
        max_text_characters: int = 120_000,
        health_cache_seconds: float = 300.0,
    ) -> None:
        self.timeout_seconds = max(0.1, min(float(timeout_seconds), 120.0))
        self.resolution_timeout_seconds = min(
            _DEFAULT_DNS_TIMEOUT_SECONDS,
            self.timeout_seconds,
        )
        self.connect_budget_seconds = min(8.0, self.timeout_seconds)
        self.max_resolved_addresses = _MAX_RESOLVED_ADDRESSES
        self.max_bytes = max(1_024, min(int(max_bytes), 10_000_000))
        self.max_text_characters = max(
            1_000,
            min(int(max_text_characters), 500_000),
        )
        self.health_cache_seconds = max(30.0, min(float(health_cache_seconds), 3600.0))
        self._last_success_at = 0.0
        self._client = httpx.AsyncClient(
            transport=_PinnedPublicTransport(
                resolution_timeout_seconds=self.resolution_timeout_seconds,
                max_addresses=self.max_resolved_addresses,
                connect_budget_seconds=self.connect_budget_seconds,
            ),
            timeout=httpx.Timeout(
                self.timeout_seconds,
                connect=self.connect_budget_seconds,
            ),
            follow_redirects=False,
            trust_env=False,
            headers={
                "User-Agent": "JarvisCore/ExternalResearch",
                "Accept-Encoding": "identity",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def invalidate_health_cache(self) -> None:
        self._last_success_at = 0.0

    async def health(self) -> dict[str, Any]:
        if (
            self.health_cache_seconds > 0
            and self._last_success_at
            and time.monotonic() - self._last_success_at <= self.health_cache_seconds
        ):
            return {"healthy": True, "reason": None}
        try:
            await self.fetch("https://example.com/")
        except Exception as exc:
            return {
                "healthy": False,
                "reason": f"Public web fetch probe failed ({type(exc).__name__}).",
            }
        return {"healthy": True, "reason": None}

    async def fetch(self, url: str) -> FetchedPage:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await self._fetch_within_deadline(url)
        except TimeoutError as exc:
            raise RuntimeError("The public page fetch timed out") from exc

    async def _fetch_within_deadline(self, url: str) -> FetchedPage:
        current = await assert_public_url(
            url,
            timeout_seconds=self.resolution_timeout_seconds,
            max_addresses=self.max_resolved_addresses,
        )
        final_page: FetchedPage | None = None
        for _ in range(6):
            async with self._stateless_stream(current) as streamed:
                if streamed.status_code in {301, 302, 303, 307, 308}:
                    location = streamed.headers.get("location", "").strip()
                    if not location:
                        raise RuntimeError("The page returned an empty redirect")
                    current = await assert_public_url(
                        urljoin(current, location),
                        timeout_seconds=self.resolution_timeout_seconds,
                        max_addresses=self.max_resolved_addresses,
                    )
                    continue
                streamed.raise_for_status()
                content_encoding = streamed.headers.get("content-encoding", "").strip().casefold()
                if content_encoding and content_encoding != "identity":
                    raise RuntimeError("The page ignored the safe identity-encoding request")
                content_type = streamed.headers.get("content-type", "").split(";", 1)[0].lower()
                supported = {
                    "text/html",
                    "application/xhtml+xml",
                    "text/plain",
                    "application/json",
                }
                if content_type not in supported:
                    raise RuntimeError("The page did not return a supported text content type")
                raw_length = streamed.headers.get("content-length", "").strip()
                if raw_length:
                    try:
                        declared_length = int(raw_length)
                    except ValueError:
                        declared_length = 0
                    if declared_length > self.max_bytes:
                        raise RuntimeError("The page exceeded the configured size limit")
                encoding_probe = httpx.Response(
                    streamed.status_code,
                    headers=streamed.headers,
                    content=b"",
                )
                decoder = codecs.getincrementaldecoder(encoding_probe.encoding or "utf-8")(
                    errors="replace"
                )
                parser = (
                    _TextExtractor(
                        max_text_characters=self.max_text_characters,
                    )
                    if content_type in {"text/html", "application/xhtml+xml"}
                    else None
                )
                plain_text = (
                    _BoundedTextAccumulator(
                        self.max_text_characters,
                        strip_leading=True,
                    )
                    if parser is None
                    else None
                )

                def consume(chunk: bytes, *, final: bool = False) -> None:
                    decoded = decoder.decode(chunk, final=final)
                    if not decoded:
                        return
                    if parser is not None:
                        parser.feed(decoded)
                    elif plain_text is not None:
                        plain_text.append(decoded)

                received = 0
                if streamed.is_stream_consumed:
                    received = len(streamed.content)
                    if received > self.max_bytes:
                        raise RuntimeError("The page exceeded the configured size limit")
                    consume(streamed.content)
                else:
                    async for chunk in streamed.aiter_raw():
                        received += len(chunk)
                        if received > self.max_bytes:
                            raise RuntimeError("The page exceeded the configured size limit")
                        consume(chunk)
                consume(b"", final=True)

                title = current
                published_at: str | None = None
                updated_at: str | None = streamed.headers.get("last-modified")
                canonical = current
                if parser is not None:
                    parser.close()
                    title = parser.title or current
                    text = parser.text
                    if parser.canonical:
                        try:
                            candidate = canonicalize_url(urljoin(current, parser.canonical))
                            current_host = urlsplit(current).hostname
                            candidate_host = urlsplit(candidate).hostname
                            if candidate_host == current_host:
                                canonical = candidate
                            else:
                                canonical = await assert_public_url(
                                    candidate,
                                    timeout_seconds=self.resolution_timeout_seconds,
                                    max_addresses=self.max_resolved_addresses,
                                )
                        except (ValueError, PublicURLResolutionError):
                            canonical = current
                    published_at = parser.published_at
                    updated_at = parser.updated_at or updated_at
                else:
                    if plain_text is None:  # pragma: no cover - construction guard
                        raise AssertionError("Text accumulator was not initialized")
                    text = plain_text.value
                if not text:
                    raise RuntimeError("The page contained no extractable text")
                final_page = FetchedPage(
                    url=current,
                    canonical_url=canonical,
                    title=title[:500],
                    text=text,
                    content_type=content_type,
                    status_code=streamed.status_code,
                    retrieved_at=_utc_now(),
                    published_at=published_at,
                    updated_at=updated_at,
                )
                break
        else:
            raise RuntimeError("The page exceeded the redirect limit")
        if final_page is None:
            raise RuntimeError("No page response was received")
        self._last_success_at = time.monotonic()
        return final_page

    @asynccontextmanager
    async def _stateless_stream(self, url: str):
        request = self._client.build_request("GET", url)
        # httpx's pooled client retains response cookies by default. Strip the
        # synthesized header from every fully built request so one public
        # origin can never plant state that is replayed later, including when
        # fetches overlap.
        request.headers.pop("cookie", None)
        response = await self._client.send(
            request,
            stream=True,
            follow_redirects=False,
        )
        try:
            yield response
        finally:
            await response.aclose()
