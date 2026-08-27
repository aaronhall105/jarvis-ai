"""Connector adapter for the real OpenAI web-search and HTTP fetch clients."""

from __future__ import annotations

from app.connectors.base import (
    CapabilityAccess,
    CapabilityMetadata,
    CapabilityRequest,
    Connector,
    ConnectorResult,
    ProviderStatus,
    RiskLevel,
)
from app.openai_web_search import OpenAIWebSearchClient, SafeWebFetcher


OPENAI_WEB_SEARCH_CAPABILITY = CapabilityMetadata(
    "web.search",
    "openai_web_search",
    "Search the live web",
    "Provider-backed current web search with source URLs and retrieval time.",
    access=CapabilityAccess.READ,
    required_scopes=frozenset({"web:read"}),
    risk=RiskLevel.LOW,
    timeout_seconds=50,
    repeatable=True,
    minimum_poll_interval_seconds=3600,
    maximum_monitor_polls=168,
    monitor_ttl_seconds=7 * 86400,
    monitor_value_paths=("answer",),
)

PUBLIC_FETCH_CAPABILITY = CapabilityMetadata(
    "web.fetch",
    "public_web_fetch",
    "Fetch a public web page",
    "Bounded public HTTP(S) retrieval with semantic text extraction.",
    access=CapabilityAccess.READ,
    required_scopes=frozenset({"web:read"}),
    risk=RiskLevel.LOW,
    timeout_seconds=25,
    repeatable=True,
    minimum_poll_interval_seconds=300,
    maximum_monitor_polls=2016,
    monitor_ttl_seconds=7 * 86400,
    monitor_value_paths=("text", "title", "status_code"),
)


class OpenAIWebSearchConnector(Connector):
    """Live search only; its health reflects actual OpenAI search access."""

    def __init__(self, *, search: OpenAIWebSearchClient) -> None:
        super().__init__(
            provider_id="openai_web_search",
            name="OpenAI web search",
            capabilities=(OPENAI_WEB_SEARCH_CAPABILITY,),
        )
        self.search_client = search

    async def status(self) -> ProviderStatus:
        health = await self.search_client.health()
        available = bool(health.get("healthy"))
        return ProviderStatus(
            provider_id=self.provider_id,
            name=self.name,
            configured=bool(health.get("configured")),
            authenticated=bool(health.get("authenticated")),
            healthy=available,
            health_reason=str(health.get("reason") or "")[:500] or None,
            setup_requirements=(
                ()
                if health.get("configured")
                else (
                    "Configure OPENAI_API_KEY and a web-search-capable model.",
                    "Enable JARVIS_WEB_SEARCH_ENABLED.",
                )
            ),
            scopes=frozenset({"web:read"}) if available else frozenset(),
            executable_capabilities=("web.search",) if available else (),
        )

    async def execute(
        self,
        capability: CapabilityMetadata,
        request: CapabilityRequest,
    ) -> ConnectorResult:
        query = str(request.payload.get("query") or "").strip()
        if not query:
            return ConnectorResult.failed("Search query cannot be empty")
        try:
            limit = max(1, min(int(request.payload.get("limit", 8)), 20))
        except (TypeError, ValueError):
            return ConnectorResult.failed("Search limit must be an integer")
        evidence = await self.search_client.search(query, limit=limit)
        return ConnectorResult.succeeded(
            evidence.as_dict(),
            provider_reference=evidence.provider_reference,
        )

    async def aclose(self) -> None:
        await self.search_client.aclose()

    def invalidate_health_cache(self) -> None:
        invalidate = getattr(self.search_client, "invalidate_health_cache", None)
        if callable(invalidate):
            invalidate()


class PublicWebFetchConnector(Connector):
    """Credential-free bounded fetch provider, independent of search health."""

    def __init__(self, *, fetcher: SafeWebFetcher, enabled: bool = True) -> None:
        super().__init__(
            provider_id="public_web_fetch",
            name="Public web fetch",
            capabilities=(PUBLIC_FETCH_CAPABILITY,),
        )
        self.fetcher = fetcher
        self.enabled = bool(enabled)

    async def status(self) -> ProviderStatus:
        health = (
            await self.fetcher.health()
            if self.enabled
            else {"healthy": False, "reason": "External agent mode is disabled"}
        )
        available = self.enabled and bool(health.get("healthy"))
        return ProviderStatus(
            provider_id=self.provider_id,
            name=self.name,
            configured=self.enabled,
            authenticated=self.enabled,
            healthy=available,
            health_reason=str(health.get("reason") or "")[:500] or None,
            setup_requirements=(() if self.enabled else ("Enable JARVIS_EXTERNAL_AGENT_ENABLED.",)),
            scopes=frozenset({"web:read"}) if available else frozenset(),
            executable_capabilities=("web.fetch",) if available else (),
        )

    async def execute(
        self,
        capability: CapabilityMetadata,
        request: CapabilityRequest,
    ) -> ConnectorResult:
        url = str(request.payload.get("url") or "").strip()
        if not url:
            return ConnectorResult.failed("Fetch URL cannot be empty")
        try:
            page = await self.fetcher.fetch(url)
        except ValueError as exc:
            return ConnectorResult.failed(str(exc))
        return ConnectorResult.succeeded(
            page.as_dict(),
            provider_reference=page.canonical_url,
        )

    async def aclose(self) -> None:
        await self.fetcher.aclose()

    def invalidate_health_cache(self) -> None:
        invalidate = getattr(self.fetcher, "invalidate_health_cache", None)
        if callable(invalidate):
            invalidate()
