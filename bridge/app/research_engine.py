"""Provider-neutral, evidence-first live research orchestration.

The engine deliberately has no built-in network client.  A caller must inject a
real search operation (and, optionally, a fetch operation) before current-world
claims can be collected.  Search results are treated as evidence, not as proof
that an external action occurred.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.connectors import redact_text


SearchCallable = Callable[
    [str], Awaitable[Sequence[Mapping[str, Any]]] | Sequence[Mapping[str, Any]]
]
FetchCallable = Callable[[str], Awaitable[Mapping[str, Any] | str] | Mapping[str, Any] | str]
QueryGenerator = Callable[[str], Awaitable[Sequence[str]] | Sequence[str]]
Synthesizer = Callable[
    [str, Sequence["ResearchSource"], Sequence["ResearchConflict"]],
    Awaitable[str | Mapping[str, Any]] | str | Mapping[str, Any],
]
ConflictAnalyzer = Callable[
    [str, Sequence["ResearchSource"]],
    Awaitable[Sequence[Mapping[str, Any]]] | Sequence[Mapping[str, Any]],
]

_TRACKING_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth_token",
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "refresh_token",
    "secret",
    "token",
}
_TOKEN = re.compile(r"[a-z0-9]{2,}", re.I)
_DATE_KEYS = ("published_at", "published", "date_published")
_UPDATED_KEYS = ("updated_at", "updated", "date_modified")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _first_text(data: Mapping[str, Any], keys: Iterable[str]) -> str | None:
    for key in keys:
        value = data.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def canonicalize_url(url: str) -> str:
    """Return a stable HTTP(S) URL suitable for evidence deduplication."""

    value = str(url or "").strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    scheme = parts.scheme.casefold()
    if (
        scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
    ):
        return ""
    host = parts.hostname.casefold().rstrip(".")
    try:
        port = parts.port
    except ValueError:
        return ""
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = []
    for key, item in parse_qsl(parts.query, keep_blank_values=True):
        folded = key.casefold()
        if folded.startswith("utm_") or folded in _TRACKING_QUERY_KEYS:
            continue
        query.append((key, item))
    query.sort(key=lambda pair: (pair[0].casefold(), pair[1]))
    return urlunsplit((scheme, host, path, urlencode(query, doseq=True), ""))


def _origin(url: str, provider: str) -> str:
    try:
        host = urlsplit(url).hostname
    except ValueError:
        host = None
    return str(host or provider or "unknown").casefold()


def _safe_exception(exc: BaseException) -> str:
    return redact_text(f"{type(exc).__name__}: {exc}", max_length=500)


@dataclass(frozen=True, slots=True)
class SourceClaim:
    """A provider-supplied structured claim used only for conflict detection."""

    topic: str
    value: str
    statement: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceClaim":
        return cls(
            topic=str(data.get("topic") or "").strip(),
            value=str(data.get("value") or "").strip(),
            statement=str(data.get("statement") or "").strip(),
        )


@dataclass(frozen=True, slots=True)
class ResearchSource:
    title: str
    url: str
    canonical_url: str
    text: str
    published_at: str | None
    updated_at: str | None
    retrieved_at: str
    provider: str
    query: str
    queries: tuple[str, ...]
    origin: str
    quality_score: float
    relevance_score: float
    recency_score: float | None
    claims: tuple[SourceClaim, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["queries"] = list(self.queries)
        data["claims"] = [claim.to_dict() for claim in self.claims]
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResearchSource":
        return cls(
            title=str(data.get("title") or "Untitled source"),
            url=str(data.get("url") or data.get("canonical_url") or ""),
            canonical_url=str(data.get("canonical_url") or ""),
            text=str(data.get("text") or ""),
            published_at=_iso(data.get("published_at")),
            updated_at=_iso(data.get("updated_at")),
            retrieved_at=str(data.get("retrieved_at") or _iso(_utc_now())),
            provider=str(data.get("provider") or "unknown"),
            query=str(data.get("query") or ""),
            queries=tuple(str(item) for item in data.get("queries") or ()),
            origin=str(data.get("origin") or "unknown"),
            quality_score=float(data.get("quality_score") or 0.0),
            relevance_score=float(data.get("relevance_score") or 0.0),
            recency_score=(
                float(data["recency_score"]) if data.get("recency_score") is not None else None
            ),
            claims=tuple(SourceClaim.from_dict(item) for item in data.get("claims") or ()),
        )


@dataclass(frozen=True, slots=True)
class ResearchConflict:
    topic: str
    values: tuple[str, ...]
    source_urls: tuple[str, ...]
    description: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["values"] = list(self.values)
        data["source_urls"] = list(self.source_urls)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResearchConflict":
        return cls(
            topic=str(data.get("topic") or ""),
            values=tuple(str(item) for item in data.get("values") or ()),
            source_urls=tuple(str(item) for item in data.get("source_urls") or ()),
            description=str(data.get("description") or ""),
        )


@dataclass(frozen=True, slots=True)
class ResearchError:
    stage: str
    code: str
    message: str
    query: str | None = None
    url: str | None = None
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResearchError":
        return cls(
            stage=str(data.get("stage") or "unknown"),
            code=str(data.get("code") or "unknown_error"),
            message=str(data.get("message") or "Unknown research error"),
            query=str(data["query"]) if data.get("query") is not None else None,
            url=str(data["url"]) if data.get("url") is not None else None,
            retryable=bool(data.get("retryable")),
        )


@dataclass(slots=True)
class ResearchState:
    """Serializable state that can safely be stored and resumed later."""

    question: str
    pending_queries: list[str]
    completed_queries: list[str] = field(default_factory=list)
    sources: list[ResearchSource] = field(default_factory=list)
    errors: list[ResearchError] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: str(_iso(_utc_now())))
    updated_at: str = field(default_factory=lambda: str(_iso(_utc_now())))

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "pending_queries": list(self.pending_queries),
            "completed_queries": list(self.completed_queries),
            "sources": [source.to_dict() for source in self.sources],
            "errors": [error.to_dict() for error in self.errors],
            "started_at": self.started_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResearchState":
        return cls(
            question=str(data.get("question") or ""),
            pending_queries=[str(item) for item in data.get("pending_queries") or ()],
            completed_queries=[str(item) for item in data.get("completed_queries") or ()],
            sources=[ResearchSource.from_dict(item) for item in data.get("sources") or ()],
            errors=[ResearchError.from_dict(item) for item in data.get("errors") or ()],
            started_at=str(data.get("started_at") or _iso(_utc_now())),
            updated_at=str(data.get("updated_at") or _iso(_utc_now())),
        )


@dataclass(frozen=True, slots=True)
class ResearchResult:
    question: str
    summary: str
    sources: tuple[ResearchSource, ...]
    conflicts: tuple[ResearchConflict, ...]
    errors: tuple[ResearchError, ...]
    completed_queries: tuple[str, ...]
    pending_queries: tuple[str, ...]
    distinct_origin_count: int
    complete: bool
    live_evidence_available: bool
    synthesis_method: str
    resumable_state: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "summary": self.summary,
            "sources": [source.to_dict() for source in self.sources],
            "conflicts": [conflict.to_dict() for conflict in self.conflicts],
            "errors": [error.to_dict() for error in self.errors],
            "completed_queries": list(self.completed_queries),
            "pending_queries": list(self.pending_queries),
            "distinct_origin_count": self.distinct_origin_count,
            "complete": self.complete,
            "live_evidence_available": self.live_evidence_available,
            "synthesis_method": self.synthesis_method,
            "resumable_state": self.resumable_state,
        }


class ResearchEngine:
    """Bounded asynchronous research over explicitly injected live providers."""

    def __init__(
        self,
        search: SearchCallable | None,
        *,
        fetch: FetchCallable | None = None,
        synthesizer: Synthesizer | None = None,
        conflict_analyzer: ConflictAnalyzer | None = None,
        query_generator: QueryGenerator | None = None,
        provider_id: str = "unconfigured",
        timeout_seconds: float = 10.0,
        max_concurrency: int = 4,
        max_queries: int = 8,
        max_results_per_query: int = 8,
    ) -> None:
        self.search = search
        self.fetch = fetch
        self.synthesizer = synthesizer
        self.conflict_analyzer = conflict_analyzer
        self.query_generator = query_generator
        self.provider_id = str(provider_id or "unknown")
        self.timeout_seconds = max(0.05, min(float(timeout_seconds), 120.0))
        self.max_concurrency = max(1, min(int(max_concurrency), 16))
        self.max_queries = max(1, min(int(max_queries), 32))
        self.max_results_per_query = max(1, min(int(max_results_per_query), 50))
        self._semaphore = asyncio.Semaphore(self.max_concurrency)

    async def research(
        self,
        question: str,
        *,
        queries: Sequence[str] | None = None,
        resume: ResearchState | Mapping[str, Any] | None = None,
        max_queries_this_run: int | None = None,
    ) -> ResearchResult:
        question = str(question or "").strip()
        if not question:
            raise ValueError("A non-empty research question is required")

        if resume is not None:
            state = resume if isinstance(resume, ResearchState) else ResearchState.from_dict(resume)
            if state.question != question:
                raise ValueError("The resumable state belongs to a different research question")
        else:
            generated = list(queries or await self._generate_queries(question))
            state = ResearchState(question=question, pending_queries=self._clean_queries(generated))

        if self.search is None:
            if not any(error.code == "provider_unavailable" for error in state.errors):
                state.errors.append(
                    ResearchError(
                        stage="search",
                        code="provider_unavailable",
                        message=f"Live search provider '{self.provider_id}' is not configured.",
                        retryable=False,
                    )
                )
            state.updated_at = str(_iso(_utc_now()))
            return await self._result(state)

        run_limit = len(state.pending_queries)
        if max_queries_this_run is not None:
            run_limit = max(0, min(run_limit, int(max_queries_this_run)))
        batch = state.pending_queries[:run_limit]
        state.pending_queries = state.pending_queries[run_limit:]

        batches = await asyncio.gather(*(self._search_query(query) for query in batch))
        for query, (sources, errors) in zip(batch, batches, strict=True):
            state.completed_queries.append(query)
            state.sources.extend(sources)
            state.errors.extend(errors)
        state.sources = self._deduplicate(state.sources)
        state.updated_at = str(_iso(_utc_now()))
        return await self._result(state)

    async def _generate_queries(self, question: str) -> Sequence[str]:
        if self.query_generator is None:
            return (question,)
        try:
            generated = await self._timed_call(self.query_generator, question)
        except (asyncio.TimeoutError, TimeoutError):
            return (question,)
        except Exception:
            return (question,)
        return tuple(str(item) for item in generated) or (question,)

    def _clean_queries(self, queries: Sequence[str]) -> list[str]:
        clean: list[str] = []
        seen: set[str] = set()
        for query in queries:
            value = re.sub(r"\s+", " ", str(query or "")).strip()
            key = value.casefold()
            if not value or key in seen:
                continue
            seen.add(key)
            clean.append(value)
            if len(clean) >= self.max_queries:
                break
        if not clean:
            raise ValueError("At least one non-empty research query is required")
        return clean

    async def _search_query(self, query: str) -> tuple[list[ResearchSource], list[ResearchError]]:
        assert self.search is not None
        try:
            async with self._semaphore:
                raw_results = await self._timed_call(self.search, query)
        except (asyncio.TimeoutError, TimeoutError):
            return [], [
                ResearchError(
                    stage="search",
                    code="timeout",
                    message=f"Live search timed out for query: {query}",
                    query=query,
                    retryable=True,
                )
            ]
        except Exception as exc:
            return [], [
                ResearchError(
                    stage="search",
                    code="provider_failure",
                    message=f"Live search failed: {_safe_exception(exc)}",
                    query=query,
                    retryable=True,
                )
            ]

        if not isinstance(raw_results, Sequence) or isinstance(raw_results, (str, bytes)):
            return [], [
                ResearchError(
                    stage="search",
                    code="malformed_response",
                    message="Live search returned a malformed result collection.",
                    query=query,
                )
            ]

        tasks = [
            self._materialize(query, raw) for raw in list(raw_results)[: self.max_results_per_query]
        ]
        materialized = await asyncio.gather(*tasks)
        sources: list[ResearchSource] = []
        errors: list[ResearchError] = []
        for source, error in materialized:
            if source is not None:
                sources.append(source)
            if error is not None:
                errors.append(error)
        return sources, errors

    async def _materialize(
        self, query: str, raw: object
    ) -> tuple[ResearchSource | None, ResearchError | None]:
        if not isinstance(raw, Mapping):
            return None, ResearchError(
                stage="parse",
                code="malformed_result",
                message="Search returned a result that was not an object.",
                query=query,
            )
        initial = dict(raw)
        raw_url = _first_text(initial, ("canonical_url", "url", "link")) or ""
        canonical = canonicalize_url(raw_url)
        if not canonical:
            return None, ResearchError(
                stage="parse",
                code="malformed_url",
                message="Search returned a result without a valid HTTP(S) URL.",
                query=query,
                url=raw_url or None,
            )

        combined = initial
        fetch_error: ResearchError | None = None
        if self.fetch is not None:
            try:
                async with self._semaphore:
                    fetched = await self._timed_call(self.fetch, canonical)
                if isinstance(fetched, str):
                    fetched = {"text": fetched}
                if not isinstance(fetched, Mapping):
                    raise TypeError("fetch response was not an object or text")
                combined = {**initial, **dict(fetched)}
                fetched_url = _first_text(combined, ("canonical_url", "url", "final_url"))
                if fetched_url:
                    canonical = canonicalize_url(fetched_url) or canonical
            except (asyncio.TimeoutError, TimeoutError):
                fetch_error = ResearchError(
                    stage="fetch",
                    code="timeout",
                    message=f"Fetching source timed out: {canonical}",
                    query=query,
                    url=canonical,
                    retryable=True,
                )
            except Exception as exc:
                fetch_error = ResearchError(
                    stage="fetch",
                    code="provider_failure",
                    message=f"Fetching source failed: {_safe_exception(exc)}",
                    query=query,
                    url=canonical,
                    retryable=True,
                )

        title = _first_text(combined, ("title", "name")) or "Untitled source"
        text = _first_text(combined, ("text", "content", "snippet", "description")) or ""
        provider = _first_text(combined, ("provider", "source_provider")) or self.provider_id
        claims = self._claims(combined)
        published = _iso(_first_text(combined, _DATE_KEYS))
        updated = _iso(_first_text(combined, _UPDATED_KEYS))
        source = ResearchSource(
            title=title,
            url=canonical,
            canonical_url=canonical,
            text=text,
            published_at=published,
            updated_at=updated,
            retrieved_at=str(_iso(_utc_now())),
            provider=provider,
            query=query,
            queries=(query,),
            origin=_origin(canonical, provider),
            quality_score=self._quality(canonical, title, text),
            relevance_score=self._relevance(query, title, text),
            recency_score=self._recency(updated or published),
            claims=claims,
        )
        return source, fetch_error

    @staticmethod
    def _claims(data: Mapping[str, Any]) -> tuple[SourceClaim, ...]:
        raw_claims = data.get("claims")
        if isinstance(raw_claims, Sequence) and not isinstance(raw_claims, (str, bytes)):
            claims = [
                SourceClaim.from_dict(item) for item in raw_claims if isinstance(item, Mapping)
            ]
        elif data.get("claim_topic") is not None and data.get("claim_value") is not None:
            claims = [
                SourceClaim(
                    topic=str(data["claim_topic"]),
                    value=str(data["claim_value"]),
                    statement=str(data.get("claim_statement") or ""),
                )
            ]
        else:
            claims = []
        return tuple(claim for claim in claims if claim.topic and claim.value)

    @staticmethod
    def _quality(url: str, title: str, text: str) -> float:
        score = 0.35
        if url.startswith("https://"):
            score += 0.15
        if title and title != "Untitled source":
            score += 0.15
        if len(text) >= 80:
            score += 0.2
        if len(text) >= 500:
            score += 0.1
        return round(min(score, 1.0), 3)

    @staticmethod
    def _relevance(query: str, title: str, text: str) -> float:
        query_tokens = {token.casefold() for token in _TOKEN.findall(query)}
        if not query_tokens:
            return 0.0
        evidence_tokens = {token.casefold() for token in _TOKEN.findall(f"{title} {text}")}
        return round(len(query_tokens & evidence_tokens) / len(query_tokens), 3)

    @staticmethod
    def _recency(value: str | None) -> float | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None
        days = max(0.0, (_utc_now() - parsed.astimezone(timezone.utc)).total_seconds() / 86400)
        return round(math.exp(-days / 365.0), 3)

    @staticmethod
    def _deduplicate(sources: Sequence[ResearchSource]) -> list[ResearchSource]:
        deduped: dict[str, ResearchSource] = {}
        for source in sources:
            existing = deduped.get(source.canonical_url)
            if existing is None:
                deduped[source.canonical_url] = source
                continue
            queries = tuple(dict.fromkeys((*existing.queries, *source.queries)))
            claims = tuple(
                {
                    (claim.topic.casefold(), claim.value.casefold(), claim.statement): claim
                    for claim in (*existing.claims, *source.claims)
                }.values()
            )
            preferred = source if len(source.text) > len(existing.text) else existing
            deduped[source.canonical_url] = replace(
                preferred,
                query=queries[0],
                queries=queries,
                claims=claims,
                quality_score=max(existing.quality_score, source.quality_score),
                relevance_score=max(existing.relevance_score, source.relevance_score),
                recency_score=max(
                    (
                        score
                        for score in (existing.recency_score, source.recency_score)
                        if score is not None
                    ),
                    default=None,
                ),
            )
        return sorted(
            deduped.values(),
            key=lambda item: (item.relevance_score, item.quality_score, item.recency_score or -1),
            reverse=True,
        )

    @staticmethod
    def _conflicts(sources: Sequence[ResearchSource]) -> tuple[ResearchConflict, ...]:
        topics: dict[str, dict[str, set[str]]] = {}
        labels: dict[str, str] = {}
        for source in sources:
            for claim in source.claims:
                topic_key = claim.topic.casefold().strip()
                value_key = claim.value.casefold().strip()
                labels.setdefault(topic_key, claim.topic)
                topics.setdefault(topic_key, {}).setdefault(value_key, set()).add(
                    source.canonical_url
                )
        conflicts: list[ResearchConflict] = []
        for topic, values in topics.items():
            if len(values) < 2:
                continue
            support_by_url: dict[str, set[str]] = {}
            for value, urls_for_value in values.items():
                for url in urls_for_value:
                    support_by_url.setdefault(url, set()).add(value)
            urls = tuple(sorted(support_by_url))
            source_bound_disagreement = any(
                bool(support_by_url[left] - support_by_url[right])
                and bool(support_by_url[right] - support_by_url[left])
                for index, left in enumerate(urls)
                for right in urls[index + 1 :]
            )
            if not source_bound_disagreement:
                continue
            rendered_values = tuple(sorted(values))
            conflicts.append(
                ResearchConflict(
                    topic=labels[topic],
                    values=rendered_values,
                    source_urls=urls,
                    description=(
                        f"Sources report conflicting values for {labels[topic]}: "
                        + ", ".join(rendered_values)
                    ),
                )
            )
        return tuple(conflicts)

    @staticmethod
    def _validated_analyzer_conflicts(
        raw_conflicts: Sequence[Mapping[str, Any]],
        sources: Sequence[ResearchSource],
    ) -> tuple[ResearchConflict, ...]:
        """Accept only conflicts with literal, source-specific provenance.

        Analyzer output is advisory.  A cited URL is retained only when its own
        evidence contains at least one reported value, and a conflict requires
        two sources with mutually distinct value support.  This prevents one
        source containing every value from laundering an unrelated second URL
        into an apparent multi-source disagreement.
        """

        by_url = {source.canonical_url: source for source in sources}
        validated: list[ResearchConflict] = []
        seen: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
        for raw in raw_conflicts[:20]:
            topic = str(raw.get("topic") or "").strip()[:500]
            description = str(raw.get("description") or "").strip()[:2_000]
            values_by_key: dict[str, str] = {}
            for item in raw.get("values") or ():
                value = str(item).strip()[:500]
                if value:
                    values_by_key.setdefault(value.casefold(), value)
            values = tuple(values_by_key.values())

            candidate_urls: list[str] = []
            for item in raw.get("source_urls") or ():
                canonical = canonicalize_url(str(item))
                if canonical in by_url and canonical not in candidate_urls:
                    candidate_urls.append(canonical)
            if not topic or len(values) < 2 or len(candidate_urls) < 2:
                continue

            support_by_url: dict[str, frozenset[str]] = {}
            for url in candidate_urls:
                source = by_url[url]
                evidence = f"{source.title}\n{source.text}".casefold()
                supported = frozenset(
                    value_key for value_key in values_by_key if value_key in evidence
                )
                if supported:
                    support_by_url[url] = supported
            urls = tuple(support_by_url)
            if len(urls) < 2:
                continue
            if set().union(*support_by_url.values()) != set(values_by_key):
                continue

            source_bound_disagreement = any(
                bool(support_by_url[left] - support_by_url[right])
                and bool(support_by_url[right] - support_by_url[left])
                for index, left in enumerate(urls)
                for right in urls[index + 1 :]
            )
            if not source_bound_disagreement:
                continue
            key = (topic.casefold(), values, urls)
            if key in seen:
                continue
            seen.add(key)
            validated.append(
                ResearchConflict(
                    topic=topic,
                    values=values,
                    source_urls=urls,
                    description=(
                        description or f"Supplied sources report incompatible values for {topic}."
                    ),
                )
            )
        return tuple(validated)

    async def _result(self, state: ResearchState) -> ResearchResult:
        conflicts = list(self._conflicts(state.sources))
        if (
            self.conflict_analyzer is not None
            and len(state.sources) >= 2
            and not state.pending_queries
        ):
            try:
                raw_conflicts = await self._timed_call(
                    self.conflict_analyzer,
                    state.question,
                    tuple(state.sources),
                )
                if not isinstance(raw_conflicts, Sequence) or isinstance(
                    raw_conflicts, (str, bytes)
                ):
                    raise TypeError("conflict analyzer did not return a collection")
                analyzed = self._validated_analyzer_conflicts(
                    [item for item in raw_conflicts if isinstance(item, Mapping)],
                    state.sources,
                )
                existing = {
                    (
                        item.topic.casefold(),
                        tuple(value.casefold() for value in item.values),
                        item.source_urls,
                    )
                    for item in conflicts
                }
                conflicts.extend(
                    item
                    for item in analyzed
                    if (
                        item.topic.casefold(),
                        tuple(value.casefold() for value in item.values),
                        item.source_urls,
                    )
                    not in existing
                )
            except (asyncio.TimeoutError, TimeoutError):
                state.errors.append(
                    ResearchError(
                        stage="conflict_analysis",
                        code="timeout",
                        message=(
                            "Source conflict analysis timed out; collected evidence "
                            "remains available."
                        ),
                        retryable=True,
                    )
                )
            except Exception as exc:
                state.errors.append(
                    ResearchError(
                        stage="conflict_analysis",
                        code="analysis_failure",
                        message=f"Source conflict analysis failed: {_safe_exception(exc)}",
                        retryable=True,
                    )
                )
        conflicts_value = tuple(conflicts)
        distinct_origins = len({source.origin for source in state.sources})
        method = "evidence_only"
        if not state.sources:
            summary = "No live evidence was collected. No current-world conclusion can be made."
        elif self.synthesizer is None:
            conflict_note = (
                f" {len(conflicts_value)} explicit conflict(s) are present in the structured evidence."
                if conflicts_value
                else ""
            )
            summary = (
                f"Collected {len(state.sources)} live source(s) from {distinct_origins} distinct "
                f"origin(s). Review the cited evidence for factual conclusions.{conflict_note}"
            )
        else:
            try:
                synthesized = await self._timed_call(
                    self.synthesizer, state.question, tuple(state.sources), conflicts
                )
                if isinstance(synthesized, Mapping):
                    summary = str(synthesized.get("summary") or "").strip()
                else:
                    summary = str(synthesized or "").strip()
                if not summary:
                    raise ValueError("synthesizer returned an empty summary")
                method = "injected_synthesizer"
            except (asyncio.TimeoutError, TimeoutError):
                state.errors.append(
                    ResearchError(
                        stage="synthesis",
                        code="timeout",
                        message="Research synthesis timed out; evidence remains available.",
                        retryable=True,
                    )
                )
                summary = "Synthesis failed; review the structured live evidence directly."
            except Exception as exc:
                state.errors.append(
                    ResearchError(
                        stage="synthesis",
                        code="synthesis_failure",
                        message=f"Research synthesis failed: {_safe_exception(exc)}",
                    )
                )
                summary = "Synthesis failed; review the structured live evidence directly."

        return ResearchResult(
            question=state.question,
            summary=summary,
            sources=tuple(state.sources),
            conflicts=conflicts_value,
            errors=tuple(state.errors),
            completed_queries=tuple(state.completed_queries),
            pending_queries=tuple(state.pending_queries),
            distinct_origin_count=distinct_origins,
            complete=not state.pending_queries,
            live_evidence_available=bool(state.sources),
            synthesis_method=method,
            resumable_state=state.to_dict(),
        )

    async def _timed_call(self, operation: Callable[..., Any], *args: Any) -> Any:
        async def invoke() -> Any:
            result = operation(*args)
            return await result if inspect.isawaitable(result) else result

        return await asyncio.wait_for(invoke(), timeout=self.timeout_seconds)


__all__ = [
    "ResearchConflict",
    "ResearchEngine",
    "ResearchError",
    "ResearchResult",
    "ResearchSource",
    "ResearchState",
    "SourceClaim",
    "canonicalize_url",
]
