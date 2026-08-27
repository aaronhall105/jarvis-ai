"""Low-overhead production observability for Jarvis Core."""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from typing import Any


PERFORMANCE_BUDGETS_MS: dict[str, int] = {
    "speech_end_to_transcript": 900,
    "speech_end_to_model_request": 250,
    "model_request_to_first_token": 1400,
    "speech_end_to_first_audio": 2200,
    "home_assistant_command": 1200,
    "jarvis_request_total_ms": 3500,
}


class RuntimeMetrics:
    """Thread-safe rolling counters and latency samples.

    This intentionally avoids an external telemetry dependency. It gives the local
    diagnostics API enough data to diagnose latency and recovery without exporting
    household data or prompts.
    """

    def __init__(self, *, max_samples: int = 256) -> None:
        self._started = time.monotonic()
        self._max_samples = max(16, int(max_samples))
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = {}
        self._latencies: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self._max_samples)
        )
        self._last_error: dict[str, Any] | None = None

    def increment(self, name: str, amount: int = 1) -> None:
        key = str(name or "").strip()
        if not key:
            return
        with self._lock:
            self._counters[key] += int(amount)

    def observe(self, name: str, value_ms: Any) -> None:
        key = str(name or "").strip()
        if not key:
            return
        try:
            value = float(value_ms)
        except (TypeError, ValueError):
            return
        if not math.isfinite(value) or value < 0:
            return
        with self._lock:
            self._latencies[key].append(value)

    def set_gauge(self, name: str, value: Any) -> None:
        key = str(name or "").strip()
        if not key:
            return
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return
        if not math.isfinite(numeric):
            return
        with self._lock:
            self._gauges[key] = numeric

    def observe_many(self, timings: Any) -> None:
        if not isinstance(timings, dict):
            return
        for key, value in timings.items():
            if str(key).endswith("_ms"):
                self.observe(str(key), value)

    def record_error(self, source: str, message: str) -> None:
        payload = {
            "source": str(source or "unknown")[:100],
            "message": " ".join(str(message or "unknown error").split())[:500],
            "at_unix": round(time.time(), 3),
        }
        with self._lock:
            self._counters["errors"] += 1
            self._last_error = payload

    @staticmethod
    def _summary(values: list[float]) -> dict[str, Any]:
        if not values:
            return {"count": 0, "latest_ms": None, "p50_ms": None, "p95_ms": None}
        ordered = sorted(values)
        p50 = ordered[min(len(ordered) - 1, round((len(ordered) - 1) * 0.50))]
        p95 = ordered[min(len(ordered) - 1, round((len(ordered) - 1) * 0.95))]
        return {
            "count": len(values),
            "latest_ms": round(values[-1], 1),
            "p50_ms": round(p50, 1),
            "p95_ms": round(p95, 1),
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            latencies = {
                key: self._summary(list(values))
                for key, values in self._latencies.items()
            }
            last_error = dict(self._last_error) if self._last_error else None
        return {
            "uptime_seconds": max(0, round(time.monotonic() - self._started)),
            "counters": counters,
            "gauges": gauges,
            "latencies": latencies,
            "performance_budgets_ms": dict(PERFORMANCE_BUDGETS_MS),
            "last_error": last_error,
        }


def configuration_report(settings: Any, realtime_status: Any) -> dict[str, Any]:
    """Return redacted startup/readiness configuration findings."""

    errors: list[str] = []
    warnings: list[str] = []

    if not str(getattr(settings, "openai_api_key", "") or "").strip():
        errors.append("OPENAI_API_KEY is not configured")

    ha_url = str(getattr(settings, "home_assistant_url", "") or "").strip()
    if not ha_url:
        errors.append("HOME_ASSISTANT_URL is not configured")
    elif not ha_url.startswith(("http://", "https://")):
        errors.append("HOME_ASSISTANT_URL must start with http:// or https://")

    if not str(getattr(settings, "home_assistant_token", "") or "").strip():
        errors.append("HOME_ASSISTANT_TOKEN is not configured")

    if isinstance(realtime_status, dict):
        if bool(realtime_status.get("enabled")) and not bool(realtime_status.get("configured")):
            warnings.append(
                "Realtime voice is enabled but neither mobile nor Voice PE authentication is fully configured"
            )
        if realtime_status.get("last_error"):
            warnings.append("Realtime voice has a recorded provider/session error; inspect /api/system/status")

    environment = str(getattr(settings, "jarvis_environment", "development") or "development")
    if environment.casefold() == "production":
        if not str(getattr(settings, "jarvis_memory_admin_token", "") or "").strip():
            warnings.append("JARVIS_MEMORY_ADMIN_TOKEN is unset; memory administration REST endpoints stay disabled")
        if not str(getattr(settings, "jarvis_self_improvement_admin_token", "") or "").strip():
            warnings.append("JARVIS_SELF_IMPROVEMENT_ADMIN_TOKEN is unset; improvement administration REST endpoints stay disabled")
        if not str(getattr(settings, "jarvis_integrations_admin_token", "") or "").strip():
            warnings.append(
                "JARVIS_INTEGRATIONS_ADMIN_TOKEN is unset; connector, receipt, "
                "plan and monitor administration endpoints stay disabled"
            )

    if bool(getattr(settings, "jarvis_external_agent_enabled", False)) and not bool(
        getattr(settings, "jarvis_web_search_enabled", False)
    ):
        warnings.append(
            "External agent mode is enabled but live web search is disabled"
        )

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "environment": environment,
    }


runtime_metrics = RuntimeMetrics()
