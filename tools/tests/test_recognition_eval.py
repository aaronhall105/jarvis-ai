import importlib.util
import sys
from pathlib import Path


PATH = Path(__file__).parents[1] / "recognition_eval.py"
SPEC = importlib.util.spec_from_file_location("recognition_eval", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_scores_word_wake_command_interruption_and_latency() -> None:
    result = MODULE.evaluate([
        {
            "case_id": "quiet-near",
            "expected_text": "turn on the TV",
            "transcript": "turn on the TV",
            "wake_detected": True,
            "command_correct": True,
            "speech_end_to_transcript_ms": 500,
            "speech_end_to_first_audio_ms": 1200,
        },
        {
            "case_id": "tv-far-stop",
            "expected_text": "stop",
            "transcript": "stock",
            "wake_detected": True,
            "command_correct": False,
            "interruption_expected": True,
            "interruption_correct": True,
            "speech_end_to_transcript_ms": 900,
            "speech_end_to_first_audio_ms": 1800,
        },
    ])
    assert result.summary["word_error_rate"] == 0.2
    assert result.summary["wake_success_rate"] == 1.0
    assert result.summary["command_success_rate"] == 0.5
    assert result.summary["interruption_success_rate"] == 1.0
    assert result.summary["speech_end_to_transcript_p95_ms"] == 900.0
    assert result.failures[0]["case_id"] == "tv-far-stop"


def test_release_gate_rejects_any_quality_or_latency_regression() -> None:
    baseline = {
        "word_error_rate": 0.1,
        "wake_success_rate": 0.95,
        "command_success_rate": 0.95,
        "interruption_success_rate": 1.0,
        "speech_end_to_transcript_p95_ms": 900,
        "speech_end_to_first_audio_p95_ms": 2000,
    }
    candidate = dict(baseline, command_success_rate=0.9, speech_end_to_transcript_p95_ms=950)
    reasons = MODULE.regression_reasons(candidate, baseline)
    assert len(reasons) == 2
