from app.system_diagnostics import build_voice_reliability_report


def test_report_is_honest_about_offline_boundaries_and_privacy() -> None:
    report = build_voice_reliability_report(
        home_assistant_connected=True,
        realtime={"last_error": None},
        runtime={"counters": {}, "latencies": {}},
    )
    assert report["health"] == "healthy"
    assert report["degraded_modes"]["wake_word_without_internet"] is True
    assert report["degraded_modes"]["voice_transcription_without_internet"] is False
    assert report["privacy"]["audio_retained_for_metrics"] is False
    assert report["evaluation_gate"]["regressions_allowed"] is False


def test_report_turns_metrics_into_specific_recommendations() -> None:
    report = build_voice_reliability_report(
        home_assistant_connected=False,
        realtime={"last_error": "provider timeout"},
        runtime={
            "counters": {
                "voice_transcripts_completed": 20,
                "recognition_clarifications": 6,
                "voice_wake_claimed": 10,
                "voice_wake_contention_rejected": 5,
            },
            "latencies": {
                "speech_start_to_transcript_ms": {"p95_ms": 2500},
            },
        },
    )
    assert report["health"] == "degraded"
    assert {item["code"] for item in report["recommendations"]} == {
        "home_assistant_offline",
        "realtime_provider_error",
        "slow_transcription",
        "frequent_misunderstanding",
        "satellite_contention",
    }
