from __future__ import annotations

from typing import Any


def _p95(runtime: dict[str, Any], name: str) -> float | None:
    value = ((runtime.get("latencies") or {}).get(name) or {}).get("p95_ms")
    return float(value) if isinstance(value, (int, float)) else None


def build_voice_reliability_report(
    *,
    home_assistant_connected: bool,
    realtime: dict[str, Any],
    runtime: dict[str, Any],
) -> dict[str, Any]:
    counters = runtime.get("counters") or {}
    recommendations: list[dict[str, str]] = []
    health = "healthy"

    if not home_assistant_connected:
        health = "degraded"
        recommendations.append(
            {
                "code": "home_assistant_offline",
                "action": "Check the Home Assistant host, network and access token.",
            }
        )
    if realtime.get("last_error"):
        health = "degraded"
        recommendations.append(
            {
                "code": "realtime_provider_error",
                "action": "Inspect the latest realtime provider error before changing microphone tuning.",
            }
        )
    transcript_p95 = _p95(runtime, "speech_start_to_transcript_ms")
    if transcript_p95 is not None and transcript_p95 > 2200:
        health = "degraded"
        recommendations.append(
            {
                "code": "slow_transcription",
                "action": "Check Core/provider network latency; microphone gain is not the likely cause.",
            }
        )
    clarification_count = int(counters.get("recognition_clarifications", 0) or 0)
    transcript_count = int(counters.get("voice_transcripts_completed", 0) or 0)
    if transcript_count >= 10 and clarification_count / transcript_count > 0.20:
        health = "degraded"
        recommendations.append(
            {
                "code": "frequent_misunderstanding",
                "action": "Review learned pronunciations and run the distance/TV recognition corpus.",
            }
        )
    rejected = int(counters.get("voice_wake_contention_rejected", 0) or 0)
    claimed = int(counters.get("voice_wake_claimed", 0) or 0)
    if claimed >= 10 and rejected / claimed > 0.40:
        recommendations.append(
            {
                "code": "satellite_contention",
                "action": "Review satellite placement or room assignment; several units hear the same wake too often.",
            }
        )

    return {
        "health": health,
        "recommendations": recommendations,
        "privacy": {
            "audio_retained_for_metrics": False,
            "transcripts_retained_for_metrics": False,
        },
        "degraded_modes": {
            "wake_word_without_internet": True,
            "android_wake_word_without_account": True,
            "core_deterministic_commands_without_openai": True,
            "home_control_without_home_assistant": False,
            "voice_transcription_without_internet": False,
        },
        "action_lifecycle": [
            "understood",
            "planned",
            "authorised",
            "sent",
            "verified_when_supported",
            "reported",
        ],
        "evaluation_gate": {
            "metrics": [
                "word_error_rate",
                "wake_success_rate",
                "command_success_rate",
                "interruption_success_rate",
                "transcript_p95_ms",
                "first_audio_p95_ms",
            ],
            "regressions_allowed": False,
        },
    }
