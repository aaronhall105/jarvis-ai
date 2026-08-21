from types import SimpleNamespace

from app.runtime_observability import RuntimeMetrics, configuration_report


def test_metrics_are_bounded_and_summarised() -> None:
    metrics = RuntimeMetrics(max_samples=16)
    for value in range(30):
        metrics.observe("jarvis_request_total_ms", value)
    metrics.increment("turns", 2)
    metrics.set_gauge("recognition_confidence", 0.93)
    snapshot = metrics.snapshot()
    assert snapshot["counters"]["turns"] == 2
    assert snapshot["gauges"]["recognition_confidence"] == 0.93
    assert snapshot["latencies"]["jarvis_request_total_ms"]["count"] == 16
    assert snapshot["latencies"]["jarvis_request_total_ms"]["latest_ms"] == 29.0


def test_configuration_report_redacts_secrets() -> None:
    settings = SimpleNamespace(
        openai_api_key="secret-openai",
        home_assistant_url="http://homeassistant.local:8123",
        home_assistant_token="secret-ha",
        jarvis_environment="production",
        jarvis_memory_admin_token="secret-memory",
        jarvis_self_improvement_admin_token="secret-improvement",
    )
    report = configuration_report(settings, {"enabled": True, "configured": True})
    assert report["valid"] is True
    assert "secret" not in repr(report)
