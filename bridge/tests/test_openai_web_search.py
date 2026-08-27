from __future__ import annotations

import asyncio
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpcore
import httpx

from app.openai_web_search import (
    OpenAIWebSearchClient,
    PublicURLRejectedError,
    PublicURLResolutionError,
    SafeWebFetcher,
    _PinnedPublicNetworkBackend,
    _PinnedPublicTransport,
    _resolve_public_addresses,
    assert_public_url,
    canonicalize_url,
)


_PUBLIC_DNS = [(2, 1, 6, "", ("93.184.216.34", 443))]


class _TrackingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.consumed = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class _Responses:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class _Client:
    def __init__(self, response: object) -> None:
        self.responses = _Responses(response)


class WebSearchClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_client_needs_no_credentials_or_sdk_client(self) -> None:
        with patch("app.openai_web_search.AsyncOpenAI") as sdk_client:
            provider = OpenAIWebSearchClient(
                api_key="",
                model="",
                enabled=False,
            )

        health = await provider.health()

        sdk_client.assert_not_called()
        self.assertFalse(provider.configured)
        self.assertFalse(health["configured"])

    async def test_owned_sdk_client_is_closed_once(self) -> None:
        sdk_client = SimpleNamespace(close=AsyncMock())
        with patch("app.openai_web_search.AsyncOpenAI", return_value=sdk_client):
            provider = OpenAIWebSearchClient(
                api_key="configured",
                model="test-model",
            )

        await provider.aclose()
        await provider.aclose()

        sdk_client.close.assert_awaited_once_with()

    async def test_injected_sdk_client_remains_caller_owned(self) -> None:
        sdk_client = SimpleNamespace(responses=SimpleNamespace(), close=AsyncMock())
        provider = OpenAIWebSearchClient(
            api_key="configured",
            model="test-model",
            client=sdk_client,
        )

        await provider.aclose()

        sdk_client.close.assert_not_awaited()

    async def test_search_requires_completed_provider_evidence_and_deduplicates(self) -> None:
        response = SimpleNamespace(
            output_text="A current, cited answer.",
            model_dump=lambda **_: {
                "id": "resp_123",
                "output_text": "A current, cited answer.",
                "output": [
                    {
                        "type": "web_search_call",
                        "status": "completed",
                        "action": {
                            "type": "search",
                            "sources": [
                                {
                                    "type": "url",
                                    "title": "Example",
                                    "url": "https://EXAMPLE.com/story?utm_source=x",
                                },
                                {
                                    "type": "url",
                                    "title": "Duplicate",
                                    "url": "https://example.com/story",
                                },
                            ],
                        },
                    },
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "annotations": [
                                    {
                                        "type": "url_citation",
                                        "title": "Second source",
                                        "url": "https://second.example/news",
                                    }
                                ],
                            }
                        ],
                    },
                ],
            },
        )
        provider = OpenAIWebSearchClient(
            api_key="configured",
            model="test-model",
            client=_Client(response),
        )

        evidence = await provider.search("latest evidence", limit=10)

        self.assertTrue(evidence.searched)
        self.assertEqual("resp_123", evidence.provider_reference)
        self.assertEqual(2, len(evidence.sources))
        self.assertEqual(
            "https://example.com/story",
            evidence.sources[0].canonical_url,
        )
        call = provider._client.responses.calls[0]
        self.assertEqual("required", call["tool_choice"])
        self.assertIn("web_search_call.action.sources", call["include"])
        self.assertFalse(call["store"])

    async def test_output_without_sources_is_not_live_evidence(self) -> None:
        response = SimpleNamespace(
            output_text="An uncited model answer",
            model_dump=lambda **_: {
                "id": "resp_empty",
                "output": [{"type": "web_search_call", "status": "completed", "action": {}}],
            },
        )
        provider = OpenAIWebSearchClient(
            api_key="configured",
            model="test-model",
            client=_Client(response),
        )

        with self.assertRaisesRegex(RuntimeError, "verifiable source evidence"):
            await provider.search("current price")

    async def test_health_redacts_provider_exception_text(self) -> None:
        provider = OpenAIWebSearchClient(
            api_key="configured",
            model="test-model",
            client=_Client(RuntimeError("secret-token-value")),
        )

        health = await provider.health()

        self.assertFalse(health["healthy"])
        self.assertTrue(health["authenticated"])
        self.assertNotIn("secret-token-value", str(health))

    async def test_health_distinguishes_authentication_failure(self) -> None:
        authentication_error = type("AuthenticationError", (RuntimeError,), {})
        provider = OpenAIWebSearchClient(
            api_key="configured",
            model="test-model",
            client=_Client(authentication_error("invalid secret")),
        )

        health = await provider.health()

        self.assertFalse(health["healthy"])
        self.assertFalse(health["authenticated"])
        self.assertNotIn("invalid secret", str(health))

    async def test_conflict_analysis_uses_strict_source_bound_structured_output(self) -> None:
        structured = {
            "conflicts": [
                {
                    "topic": "reported total",
                    "values": ["10", "12"],
                    "source_urls": [
                        "https://one.example/value",
                        "https://two.example/value",
                    ],
                    "description": "The supplied reports disagree.",
                }
            ]
        }
        response = SimpleNamespace(
            output_text=json.dumps(structured),
            model_dump=lambda **_: {
                "status": "completed",
                "output_text": json.dumps(structured),
            },
        )
        provider = OpenAIWebSearchClient(
            api_key="configured",
            model="test-model",
            client=_Client(response),
        )

        conflicts = await provider.analyze_conflicts(
            "What is the reported total?",
            [
                {
                    "title": "One",
                    "canonical_url": "https://one.example/value",
                    "text": "The reported total is 10.",
                },
                {
                    "title": "Two",
                    "canonical_url": "https://two.example/value",
                    "text": "The reported total is 12.",
                },
            ],
        )

        self.assertEqual("reported total", conflicts[0]["topic"])
        call = provider._client.responses.calls[0]
        self.assertEqual("json_schema", call["text"]["format"]["type"])
        self.assertTrue(call["text"]["format"]["strict"])
        self.assertFalse(call["store"])
        self.assertNotIn("web_search", repr(call.get("tools")))

    async def test_private_url_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "private"):
            await assert_public_url("http://127.0.0.1/admin")

    def test_canonical_url_removes_fragment_tracking_and_credentials(self) -> None:
        self.assertEqual(
            "https://example.com/p?a=1",
            canonicalize_url("HTTPS://Example.COM:443/p?utm_campaign=x&a=1#part"),
        )
        with self.assertRaisesRegex(ValueError, "Credential-bearing"):
            canonicalize_url("https://user:pass@example.com/")

    def test_canonical_url_preserves_ipv6_literal_brackets(self) -> None:
        self.assertEqual(
            "https://[2606:4700:4700::1111]/dns-query",
            canonicalize_url("HTTPS://[2606:4700:4700::1111]:443/dns-query"),
        )
        self.assertEqual(
            "https://[2606:4700:4700::1111]:8443/",
            canonicalize_url("https://[2606:4700:4700::1111]:8443"),
        )


class WebFetcherTests(unittest.IsolatedAsyncioTestCase):
    async def _fetch_with_production_client(
        self,
        handler,
        *,
        url: str = "https://example.com/story",
        dns_records: list[tuple] | None = None,
        **fetcher_kwargs,
    ):
        async def transport_handler(
            _transport: _PinnedPublicTransport,
            request: httpx.Request,
        ) -> httpx.Response:
            return await handler(request)

        with (
            patch.object(
                _PinnedPublicTransport,
                "handle_async_request",
                new=transport_handler,
            ),
            patch(
                "app.openai_web_search.socket.getaddrinfo",
                return_value=dns_records or _PUBLIC_DNS,
            ),
        ):
            fetcher = SafeWebFetcher(**fetcher_kwargs)
            try:
                return await fetcher.fetch(url)
            finally:
                await fetcher.aclose()

    async def test_non_global_addresses_are_rejected(self) -> None:
        for address in (
            "100.64.0.1",
            "100.127.255.254",
            "fec0::1",
            "::ffff:100.64.0.1",
            "224.0.0.1",
        ):
            with self.subTest(address=address):
                with self.assertRaisesRegex(PublicURLRejectedError, "non-global"):
                    await _resolve_public_addresses(address)

    async def test_mixed_dns_set_rejects_private_record_after_candidate_cap(self) -> None:
        records = [(2, 1, 6, "", (f"8.8.4.{index}", 443)) for index in range(1, 10)]
        records.append((2, 1, 6, "", ("127.0.0.1", 443)))

        with patch(
            "app.openai_web_search.socket.getaddrinfo",
            return_value=records,
        ):
            with self.assertRaisesRegex(PublicURLRejectedError, "private"):
                await _resolve_public_addresses("mixed.example", max_addresses=2)

    async def test_dns_candidates_are_capped(self) -> None:
        records = [(2, 1, 6, "", (f"8.8.4.{index}", 443)) for index in range(1, 10)]
        with patch(
            "app.openai_web_search.socket.getaddrinfo",
            return_value=records,
        ):
            addresses = await _resolve_public_addresses(
                "many.example",
                max_addresses=3,
            )

        self.assertEqual(("8.8.4.1", "8.8.4.2", "8.8.4.3"), addresses)

    async def test_dns_resolution_has_a_caller_timeout(self) -> None:
        def slow_resolution(*_args, **_kwargs):
            time.sleep(0.15)
            return _PUBLIC_DNS

        started = time.monotonic()
        with patch(
            "app.openai_web_search.socket.getaddrinfo",
            new=slow_resolution,
        ):
            with self.assertRaisesRegex(PublicURLResolutionError, "timed out"):
                await _resolve_public_addresses(
                    "slow.example",
                    timeout_seconds=0.05,
                )
            elapsed = time.monotonic() - started
            await asyncio.sleep(0.12)

        self.assertLess(elapsed, 0.13)

    async def test_connect_time_dns_rebinding_to_private_address_is_blocked(self) -> None:
        private = [(2, 1, 6, "", ("127.0.0.1", 443))]
        backend = _PinnedPublicNetworkBackend()
        backend._backend = SimpleNamespace(connect_tcp=AsyncMock())
        with patch(
            "app.openai_web_search.socket.getaddrinfo",
            side_effect=[_PUBLIC_DNS, private],
        ):
            self.assertEqual(
                "https://rebind.example/",
                await assert_public_url("https://rebind.example/"),
            )
            with self.assertRaisesRegex(ValueError, "private"):
                await backend.connect_tcp("rebind.example", 443)
        backend._backend.connect_tcp.assert_not_awaited()

    async def test_connect_attempts_share_one_budget(self) -> None:
        observed_timeouts: list[float] = []

        async def slow_failure(
            _address,
            _port,
            *,
            timeout,
            local_address,
            socket_options,
        ):
            del local_address, socket_options
            observed_timeouts.append(timeout)
            await asyncio.sleep(0.09)
            raise OSError("unreachable")

        backend = _PinnedPublicNetworkBackend(
            max_addresses=2,
            connect_budget_seconds=0.12,
        )
        backend._backend = SimpleNamespace(connect_tcp=slow_failure)
        records = [
            (2, 1, 6, "", ("8.8.8.8", 443)),
            (2, 1, 6, "", ("1.1.1.1", 443)),
        ]

        started = time.monotonic()
        with patch(
            "app.openai_web_search.socket.getaddrinfo",
            return_value=records,
        ):
            with self.assertRaises(httpcore.ConnectTimeout):
                await backend.connect_tcp("budget.example", 443, timeout=0.12)
        elapsed = time.monotonic() - started

        self.assertEqual(2, len(observed_timeouts))
        self.assertLess(observed_timeouts[1], observed_timeouts[0])
        self.assertLess(elapsed, 0.2)

    async def test_transport_preserves_original_host_and_query_for_httpcore(self) -> None:
        class RecordingPool:
            def __init__(self) -> None:
                self.request = None

            async def handle_async_request(self, request):
                self.request = request
                return httpcore.Response(
                    200,
                    headers=[(b"content-type", b"text/plain")],
                    content=b"ok",
                )

            async def aclose(self) -> None:
                return None

        transport = _PinnedPublicTransport()
        await transport._pool.aclose()
        pool = RecordingPool()
        transport._pool = pool

        response = await transport.handle_async_request(
            httpx.Request("GET", "https://example.com/path?q=1")
        )
        await response.aread()
        await response.aclose()
        await transport.aclose()

        self.assertEqual(b"example.com", pool.request.url.host)
        self.assertEqual(b"/path?q=1", pool.request.url.target)
        headers = {key.lower(): value for key, value in pool.request.headers}
        self.assertEqual(b"example.com", headers[b"host"])

    async def test_fetcher_has_no_generic_client_injection(self) -> None:
        with self.assertRaises(TypeError):
            SafeWebFetcher(client=object())  # type: ignore[call-arg]

    async def test_fetch_extracts_title_canonical_dates_and_text(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text=(
                    "<html><head><title> Example story </title>"
                    "<link rel='canonical' href='/canonical'>"
                    "<meta property='article:published_time' content='2026-08-26'>"
                    "</head><body><script>ignore me</script><p>Useful evidence.</p>"
                    "</body></html>"
                ),
                request=request,
            )

        page = await self._fetch_with_production_client(handler)

        self.assertEqual("Example story", page.title)
        self.assertEqual("https://example.com/canonical", page.canonical_url)
        self.assertEqual("2026-08-26", page.published_at)
        self.assertIn("Useful evidence.", page.text)
        self.assertNotIn("ignore me", page.text)

    async def test_private_redirect_is_rejected_before_second_request(self) -> None:
        requested: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(
                302,
                headers={"location": "http://127.0.0.1/secret"},
                request=request,
            )

        with self.assertRaisesRegex(PublicURLRejectedError, "private"):
            await self._fetch_with_production_client(handler)

        self.assertEqual(["https://example.com/story"], requested)

    async def test_private_cross_origin_canonical_is_not_exposed(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text=(
                    "<html><head><title>Public story</title>"
                    "<link rel='canonical' href='http://127.0.0.1/private'>"
                    "</head><body>Public evidence.</body></html>"
                ),
                request=request,
            )

        page = await self._fetch_with_production_client(handler)

        self.assertEqual("https://example.com/story", page.canonical_url)

    async def test_fetcher_never_replays_provider_cookies_between_requests(self) -> None:
        observed_cookies: list[str | None] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            observed_cookies.append(request.headers.get("cookie"))
            return httpx.Response(
                200,
                headers={
                    "content-type": "text/plain",
                    "set-cookie": "provider_session=must-not-replay; Path=/; Secure",
                },
                text="Public evidence.",
                request=request,
            )

        async def transport_handler(
            _transport: _PinnedPublicTransport,
            request: httpx.Request,
        ) -> httpx.Response:
            return await handler(request)

        with (
            patch.object(
                _PinnedPublicTransport,
                "handle_async_request",
                new=transport_handler,
            ),
            patch(
                "app.openai_web_search.socket.getaddrinfo",
                return_value=_PUBLIC_DNS,
            ),
        ):
            fetcher = SafeWebFetcher()
            try:
                await fetcher.fetch("https://example.com/first")
                await fetcher.fetch("https://example.com/second")
            finally:
                await fetcher.aclose()

        self.assertEqual([None, None], observed_cookies)

    async def test_unsupported_content_consumes_no_body_and_closes_stream(self) -> None:
        stream = _TrackingStream([b"binary"])

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "application/octet-stream"},
                stream=stream,
                request=request,
            )

        with self.assertRaisesRegex(RuntimeError, "supported text"):
            await self._fetch_with_production_client(handler)

        self.assertEqual(0, stream.consumed)
        self.assertTrue(stream.closed)

    async def test_encoded_content_consumes_no_body_and_closes_stream(self) -> None:
        stream = _TrackingStream([b"compressed"])

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={
                    "content-type": "text/plain",
                    "content-encoding": "gzip",
                },
                stream=stream,
                request=request,
            )

        with self.assertRaisesRegex(RuntimeError, "identity-encoding"):
            await self._fetch_with_production_client(handler)

        self.assertEqual(0, stream.consumed)
        self.assertTrue(stream.closed)

    async def test_streamed_size_limit_closes_response(self) -> None:
        stream = _TrackingStream([b"a" * 1_024, b"b"])

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/plain"},
                stream=stream,
                request=request,
            )

        with self.assertRaisesRegex(RuntimeError, "size limit"):
            await self._fetch_with_production_client(
                handler,
                max_bytes=1_024,
            )

        self.assertEqual(2, stream.consumed)
        self.assertTrue(stream.closed)

    async def test_incremental_utf8_decode_and_text_bound(self) -> None:
        euro = _TrackingStream([b"Price: \xe2", b"\x82", b"\xac"])

        async def plain_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/plain; charset=utf-8"},
                stream=euro,
                request=request,
            )

        page = await self._fetch_with_production_client(plain_handler)
        self.assertEqual("Price: €", page.text)

        html = ("<p>evidence</p>" * 2_000).encode()

        async def html_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                content=html,
                request=request,
            )

        bounded = await self._fetch_with_production_client(
            html_handler,
            max_text_characters=1_000,
        )
        self.assertEqual(1_000, len(bounded.text))
