import asyncio
import json
import unittest

from app.research_engine import ResearchEngine, canonicalize_url


class ResearchEngineTests(unittest.IsolatedAsyncioTestCase):
    def test_canonical_url_removes_tracking_fragment_and_default_port(self):
        self.assertEqual(
            "https://example.com/story?a=1&b=2",
            canonicalize_url("HTTPS://Example.COM:443/story/?utm_source=test&b=2&a=1#comments"),
        )
        self.assertEqual("", canonicalize_url("file:///etc/passwd"))

    async def test_multi_query_search_is_bounded_and_sources_are_structured(self):
        active = 0
        peak = 0

        async def search(query):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return [
                {
                    "title": f"Result for {query}",
                    "url": f"https://{query}.example/news",
                    "snippet": f"Evidence about {query}",
                    "published_at": "2026-08-25T12:00:00+00:00",
                }
            ]

        engine = ResearchEngine(
            search,
            provider_id="fixture-search",
            max_concurrency=2,
            timeout_seconds=1,
        )
        result = await engine.research("question", queries=["one", "two", "three"])

        self.assertEqual(2, peak)
        self.assertEqual(3, len(result.sources))
        self.assertEqual(3, result.distinct_origin_count)
        self.assertTrue(result.complete)
        self.assertTrue(result.live_evidence_available)
        source = result.sources[0]
        self.assertTrue(source.title)
        self.assertTrue(source.canonical_url.startswith("https://"))
        self.assertEqual("fixture-search", source.provider)
        self.assertTrue(source.query)
        self.assertTrue(source.retrieved_at)
        self.assertIsNotNone(source.published_at)
        self.assertIsNotNone(source.recency_score)

    async def test_fetch_metadata_deduplicates_canonical_url_and_merges_queries(self):
        async def search(query):
            url = (
                "https://Example.com:443/item/?utm_source=first&b=2&a=1#top"
                if query == "first"
                else "https://example.com/item?a=1&b=2"
            )
            return [{"title": "Search title", "url": url, "snippet": "short"}]

        async def fetch(url):
            return {
                "title": "Canonical page",
                "text": "A sufficiently detailed fetched page " * 20,
                "canonical_url": "https://example.com/item?b=2&a=1",
                "updated_at": "2026-08-26T01:00:00Z",
            }

        result = await ResearchEngine(search, fetch=fetch, provider_id="fixture").research(
            "item", queries=["first", "second"]
        )

        self.assertEqual(1, len(result.sources))
        self.assertEqual("https://example.com/item?a=1&b=2", result.sources[0].canonical_url)
        self.assertEqual(("first", "second"), result.sources[0].queries)
        self.assertEqual("Canonical page", result.sources[0].title)
        self.assertEqual(1, result.distinct_origin_count)

    async def test_explicit_structured_claims_produce_conflict(self):
        async def search(query):
            return [
                {
                    "title": "Official source",
                    "url": "https://official.example/value",
                    "snippet": "The reported value is 10.",
                    "claims": [{"topic": "reported total", "value": "10"}],
                },
                {
                    "title": "Independent source",
                    "url": "https://independent.example/value",
                    "snippet": "The reported value is 12.",
                    "claims": [{"topic": "reported total", "value": "12"}],
                },
            ]

        result = await ResearchEngine(search, provider_id="fixture").research("reported total")

        self.assertEqual(2, result.distinct_origin_count)
        self.assertEqual(1, len(result.conflicts))
        self.assertEqual(("10", "12"), result.conflicts[0].values)
        self.assertIn("conflict", result.summary)

    async def test_source_bound_analyzer_accepts_values_bound_to_different_sources(self):
        async def search(query):
            return [
                {
                    "title": "First report",
                    "url": "https://first.example/value",
                    "snippet": "The reported total is 10.",
                },
                {
                    "title": "Second report",
                    "url": "https://second.example/value",
                    "snippet": "The reported total is 12.",
                },
            ]

        async def analyze(question, sources):
            self.assertEqual("reported total", question)
            self.assertEqual(2, len(sources))
            return [
                {
                    "topic": "reported total",
                    "values": ["10", "12"],
                    "source_urls": [
                        "https://first.example/value",
                        "https://second.example/value",
                    ],
                    "description": "The reports disagree.",
                },
                {
                    "topic": "invented",
                    "values": ["secret", "fiction"],
                    "source_urls": [
                        "https://first.example/value",
                        "https://second.example/value",
                    ],
                    "description": "Unsupported.",
                },
            ]

        result = await ResearchEngine(
            search,
            conflict_analyzer=analyze,
            provider_id="fixture",
        ).research("reported total")

        self.assertEqual(1, len(result.conflicts))
        self.assertEqual(("10", "12"), result.conflicts[0].values)
        self.assertEqual(
            (
                "https://first.example/value",
                "https://second.example/value",
            ),
            result.conflicts[0].source_urls,
        )
        self.assertEqual((), result.errors)

    async def test_structured_claims_from_one_source_cannot_invent_multi_source_conflict(self):
        async def search(query):
            return [
                {
                    "title": "Combined report",
                    "url": "https://combined.example/value",
                    "snippet": "The report lists both 10 and 12 without resolving them.",
                    "claims": [
                        {"topic": "reported total", "value": "10"},
                        {"topic": "reported total", "value": "12"},
                    ],
                },
                {
                    "title": "Second report",
                    "url": "https://second.example/value",
                    "snippet": "The reported total is 10.",
                    "claims": [{"topic": "reported total", "value": "10"}],
                },
            ]

        result = await ResearchEngine(search, provider_id="fixture").research("reported total")

        self.assertEqual((), result.conflicts)

    async def test_source_bound_analyzer_rejects_unrelated_second_citation(self):
        async def search(query):
            return [
                {
                    "title": "Combined report",
                    "url": "https://combined.example/value",
                    "snippet": "The report discusses both totals 10 and 12.",
                },
                {
                    "title": "Unrelated report",
                    "url": "https://unrelated.example/value",
                    "snippet": "This report contains no stated total.",
                },
            ]

        async def analyze(question, sources):
            return [
                {
                    "topic": "reported total",
                    "values": ["10", "12"],
                    "source_urls": [
                        "https://combined.example/value",
                        "https://unrelated.example/value",
                    ],
                    "description": "The reports disagree.",
                }
            ]

        result = await ResearchEngine(
            search,
            conflict_analyzer=analyze,
            provider_id="fixture",
        ).research("reported total")

        self.assertEqual((), result.conflicts)
        self.assertEqual((), result.errors)

    async def test_same_origin_is_not_described_as_independent(self):
        async def search(query):
            return [
                {"title": "One", "url": "https://news.example/a", "snippet": "A"},
                {"title": "Two", "url": "https://news.example/b", "snippet": "B"},
            ]

        result = await ResearchEngine(search, provider_id="fixture").research("news")
        self.assertEqual(2, len(result.sources))
        self.assertEqual(1, result.distinct_origin_count)
        self.assertIn("1 distinct origin", result.summary)
        self.assertNotIn("2 independent", result.summary)

    async def test_unavailable_provider_never_fabricates_current_evidence(self):
        result = await ResearchEngine(None, provider_id="web-search").research("latest news")
        self.assertFalse(result.live_evidence_available)
        self.assertEqual(0, result.distinct_origin_count)
        self.assertEqual("provider_unavailable", result.errors[0].code)
        self.assertIn("No live evidence", result.summary)

    async def test_timeout_and_provider_failure_are_truthful(self):
        async def slow(query):
            await asyncio.sleep(0.1)
            return []

        timeout = await ResearchEngine(slow, timeout_seconds=0.01).research("current price")
        self.assertEqual("timeout", timeout.errors[0].code)
        self.assertFalse(timeout.live_evidence_available)

        async def failed(query):
            raise RuntimeError("provider offline")

        failure = await ResearchEngine(failed).research("current price")
        self.assertEqual("provider_failure", failure.errors[0].code)
        self.assertIn("offline", failure.errors[0].message)
        self.assertFalse(failure.live_evidence_available)

    async def test_malformed_search_and_page_are_reported_without_fake_source(self):
        async def malformed_collection(query):
            return {"url": "https://example.com"}

        result = await ResearchEngine(malformed_collection).research("question")
        self.assertEqual("malformed_response", result.errors[0].code)
        self.assertEqual((), result.sources)

        async def malformed_hits(query):
            return ["not an object", {"title": "No URL"}]

        hits = await ResearchEngine(malformed_hits).research("question")
        self.assertEqual({"malformed_result", "malformed_url"}, {e.code for e in hits.errors})
        self.assertFalse(hits.live_evidence_available)

    async def test_fetch_failure_retains_only_the_search_evidence_and_error(self):
        async def search(query):
            return [
                {
                    "title": "Search evidence",
                    "url": "https://source.example/a",
                    "snippet": "Provider supplied snippet",
                }
            ]

        async def fetch(url):
            raise RuntimeError("malformed page")

        result = await ResearchEngine(search, fetch=fetch).research("evidence")
        self.assertEqual(1, len(result.sources))
        self.assertEqual("Provider supplied snippet", result.sources[0].text)
        self.assertEqual("provider_failure", result.errors[0].code)
        self.assertEqual("fetch", result.errors[0].stage)

    async def test_resumable_state_is_json_serializable_and_continues(self):
        calls = []

        async def search(query):
            calls.append(query)
            return [{"title": query, "url": f"https://{query}.example/a", "snippet": query}]

        engine = ResearchEngine(search)
        partial = await engine.research("multi", queries=["one", "two"], max_queries_this_run=1)
        self.assertFalse(partial.complete)
        self.assertEqual(("two",), partial.pending_queries)
        serialized = json.loads(json.dumps(partial.resumable_state))

        completed = await engine.research("multi", resume=serialized)
        self.assertTrue(completed.complete)
        self.assertEqual(["one", "two"], calls)
        self.assertEqual(2, len(completed.sources))

    async def test_injected_synthesizer_receives_actual_evidence_and_conflicts(self):
        seen = {}

        async def search(query):
            return [{"title": "One", "url": "https://one.example/a", "snippet": "one"}]

        async def synthesize(question, sources, conflicts):
            seen.update(question=question, source_count=len(sources), conflict_count=len(conflicts))
            return {"summary": "Evidence-backed fixture synthesis."}

        result = await ResearchEngine(search, synthesizer=synthesize).research("question")
        self.assertEqual("injected_synthesizer", result.synthesis_method)
        self.assertEqual("Evidence-backed fixture synthesis.", result.summary)
        self.assertEqual({"question": "question", "source_count": 1, "conflict_count": 0}, seen)


if __name__ == "__main__":
    unittest.main()
