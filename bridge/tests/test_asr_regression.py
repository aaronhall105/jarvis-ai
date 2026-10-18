from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from app.asr_regression import (
    AsrObservation,
    CommandAdapter,
    CommandAsrAdapter,
    CommandSemanticAdapter,
    CorpusError,
    CriticalToken,
    CriticalTokenClass,
    RegressionHarness,
    ReplayAsrAdapter,
    ReplaySemanticAdapter,
    SafetyOutcome,
    SemanticObservation,
    assert_private_corpus_location,
    clarification_risk_recommendation,
    compare_baseline,
    load_corpus,
    parse_case,
    safety_outcome,
    word_error_counts,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / "fixtures" / "asr_regression_public.json"


def _case(**changes: Any):
    payload: dict[str, Any] = {
        "case_id": "case-1",
        "gold_transcript": "set it to thirty percent",
        "expected": {
            "task_family": "HOME",
            "semantic_operation": "CONTROL",
            "grounded_target": "capability:test-level",
            "capability": "level",
            "tool": "execute_parameter",
            "arguments": {"value": 30},
            "clarification_required": False,
            "safety_outcome": "WOULD_EXECUTE_SAFE",
        },
        "critical_tokens": [
            {"class": "PERCENTAGE", "field": "arguments.value", "expected_value": 30}
        ],
        "metadata": {"endpoint": "PHONE"},
        "observed": {
            "asr": {
                "transcript": "set it to thirty percent",
                "model": "test-asr",
                "confidence": 0.9,
                "latency_ms": 100,
            },
            "semantic": {
                "task_family": "HOME",
                "semantic_operation": "CONTROL",
                "grounded_target": "capability:test-level",
                "capability": "level",
                "tool": "execute_parameter",
                "arguments": {"value": 30},
                "clarification_required": False,
                "valid": True,
                "effect": "WRITE_REVERSIBLE",
                "execution_attempted": False,
                "timings_ms": {"understanding": 2, "planning": 20, "grounding": 3},
            },
        },
    }
    payload.update(changes)
    return parse_case(payload)


def test_public_schema_covers_every_critical_token_class() -> None:
    cases = load_corpus(FIXTURE)
    classes = {token.token_class for case in cases for token in case.critical_tokens}
    assert classes == set(CriticalTokenClass)
    assert {case.metadata.get("endpoint") for case in cases} == {
        "PHONE",
        "WATCH",
        "VOICE_DEVICE",
    }


def test_cli_accepts_documented_positional_manifest() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "run_asr_semantic_regression.py"),
            str(FIXTURE),
            "--case",
            "percentage-thirty",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Cases: 1/1 passed" in completed.stdout


def test_schema_rejects_unknown_token_class_and_duplicate_sequence_turn(tmp_path: Path) -> None:
    with pytest.raises(CorpusError, match="unsupported critical token class"):
        _case(critical_tokens=[{"class": "SECRET", "field": "x", "expected_value": "x"}])

    first = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"][0]
    first["sequence_id"] = "same"
    first["turn_index"] = 1
    second = dict(first, case_id="different")
    corpus = tmp_path / "duplicate.json"
    corpus.write_text(json.dumps({"schema_version": 1, "cases": [first, second]}), encoding="utf-8")
    with pytest.raises(CorpusError, match="duplicate sequence turn"):
        load_corpus(corpus)


def test_private_corpus_must_be_outside_repository(tmp_path: Path) -> None:
    outside = tmp_path / "private"
    outside.mkdir()
    manifest = outside / "cases.json"
    manifest.write_text("{}", encoding="utf-8")
    assert_private_corpus_location(manifest, private_root=outside, repo_root=REPO_ROOT)

    with pytest.raises(CorpusError, match="outside the Git repository"):
        assert_private_corpus_location(
            REPO_ROOT / "private-asr-regression" / "cases.json",
            private_root=REPO_ROOT / "private-asr-regression",
            repo_root=REPO_ROOT,
        )


def test_defensive_private_paths_are_gitignored() -> None:
    completed = subprocess.run(
        ["git", "check-ignore", "private-asr-regression/sample.wav", "sample.private-asr.json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "private-asr-regression/sample.wav" in completed.stdout
    assert "sample.private-asr.json" in completed.stdout


def test_word_error_scoring_distinguishes_transcript_edits() -> None:
    assert word_error_counts("set it to thirty percent", "set it to thirteen percent") == (1, 5)
    assert word_error_counts("turn on the light", "turn light on") == (3, 4)


def test_public_fixture_scores_semantics_arguments_clarification_and_tokens() -> None:
    cases = load_corpus(FIXTURE)
    report = (
        RegressionHarness(ReplayAsrAdapter(), ReplaySemanticAdapter())
        .run(
            cases,
            corpus_root=FIXTURE.parent,
            source_commit="test-sha",
        )
        .payload
    )
    summary = report["summary"]
    assert summary["case_count"] == len(cases)
    assert summary["failed"] == 0
    assert summary["task_family_accuracy"] == 1.0
    assert summary["semantic_operation_accuracy"] == 1.0
    assert summary["grounded_target_accuracy"] == 1.0
    assert summary["capability_accuracy"] == 1.0
    assert summary["tool_accuracy"] == 1.0
    assert summary["exact_argument_accuracy"] == 1.0
    assert summary["clarification_accuracy"] == 1.0
    assert summary["critical_token_accuracy"] == 1.0
    assert summary["word_error_rate"] > 0
    assert summary["timings"]["planning"]["p95_ms"] is not None
    assert report["privacy"] == {
        "audio_embedded": False,
        "transcripts_included": False,
        "private_corpus_uploaded": False,
    }


def test_report_redacts_transcripts_and_sensitive_values_by_default() -> None:
    case = _case(
        observed={
            "asr": {"transcript": "private spoken words", "confidence": 0.9},
            "semantic": {
                "task_family": "HOME",
                "semantic_operation": "CONTROL",
                "grounded_target": "wrong-private-target",
                "capability": "level",
                "tool": "execute_parameter",
                "arguments": {"value": 13},
                "clarification_required": False,
                "valid": True,
                "effect": "WRITE",
                "execution_attempted": False,
            },
        }
    )
    report = (
        RegressionHarness(ReplayAsrAdapter(), ReplaySemanticAdapter())
        .run([case], corpus_root=FIXTURE.parent)
        .payload
    )
    encoded = json.dumps(report)
    assert "private spoken words" not in encoded
    assert "wrong-private-target" not in encoded
    assert report["failures"][0]["critical_tokens"][0]["actual"] == 13


def test_sensitive_transcript_output_requires_explicit_opt_in() -> None:
    report = (
        RegressionHarness(ReplayAsrAdapter(), ReplaySemanticAdapter(), include_sensitive=True)
        .run([_case()], corpus_root=FIXTURE.parent)
        .payload
    )
    assert report["cases"][0]["transcript"] == "set it to thirty percent"


def test_missing_gold_transcript_excludes_wer_denominator() -> None:
    case = _case(gold_transcript=None)
    report = (
        RegressionHarness(ReplayAsrAdapter(), ReplaySemanticAdapter())
        .run([case], corpus_root=FIXTURE.parent)
        .payload
    )
    assert report["summary"]["word_error_rate"] is None
    assert report["cases"][0]["word_error_rate"] is None


def test_missing_audio_is_rejected_when_manifest_references_it(tmp_path: Path) -> None:
    with pytest.raises(CorpusError, match="audio file is missing"):
        RegressionHarness(ReplayAsrAdapter(), ReplaySemanticAdapter()).run(
            [_case(audio_path="missing.wav")], corpus_root=tmp_path
        )


def test_local_command_adapters_preserve_audio_endpoint_context_and_plan_only_mode(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"synthetic-test-bytes")
    script = tmp_path / "adapter.py"
    script.write_text(
        """import json, sys
payload = json.load(sys.stdin)
if payload["mode"] == "transcribe_only":
    assert payload["endpoint"] == "PHONE"
    print(json.dumps({"transcript": "set it to thirty percent", "model": "local-test"}))
else:
    assert payload == {
        "mode": "plan_only",
        "allow_execution": False,
        "case_id": "case-1",
        "transcript": "set it to thirty percent",
        "endpoint": "PHONE",
        "conversation": [{"turn": 1}],
    }
    print(json.dumps({"valid": True, "effect": "WRITE", "execution_attempted": False}))
""",
        encoding="utf-8",
    )
    command = CommandAdapter([sys.executable, str(script)])
    case = _case()
    asr = CommandAsrAdapter(command).transcribe(case, audio)
    assert asr.transcript == "set it to thirty percent"
    assert asr.model == "local-test"
    semantic = CommandSemanticAdapter(command).plan(case, asr.transcript, [{"turn": 1}])
    assert semantic.plan["execution_attempted"] is False
    assert semantic.timings_ms["semantic_adapter"] >= 0


def test_plan_only_boundary_rejects_any_execution_claim() -> None:
    with pytest.raises(CorpusError, match="attempted execution"):
        safety_outcome(
            {
                "valid": True,
                "effect": "WRITE",
                "execution_attempted": True,
            }
        )
    assert safety_outcome({"valid": True, "effect": "WRITE"}) is SafetyOutcome.WOULD_EXECUTE_SAFE
    assert safety_outcome({"valid": False, "effect": "WRITE"}) is SafetyOutcome.INVALID_PLAN
    assert safety_outcome({"valid": True, "effect": "UNKNOWN"}) is SafetyOutcome.INVALID_PLAN
    assert (
        safety_outcome({"valid": True, "effect": "WRITE", "clarification_required": True})
        is SafetyOutcome.WOULD_CLARIFY
    )
    assert (
        safety_outcome({"valid": True, "effect": "WRITE", "rejected": True})
        is SafetyOutcome.WOULD_REJECT
    )


def test_confidence_policy_uses_real_signal_and_stays_unknown_without_it() -> None:
    token = CriticalToken(CriticalTokenClass.PERCENTAGE, "arguments.value", 30)
    low = clarification_risk_recommendation(
        AsrObservation(
            transcript="set it",
            confidence=0.8,
            token_confidences={"arguments.value": 0.2},
        ),
        {"effect": "WRITE"},
        [token],
    )
    assert low["should_clarify"] is True
    assert low["basis"] == "critical_token_confidence"

    absent = clarification_risk_recommendation(
        AsrObservation(transcript="set it"), {"effect": "WRITE"}, [token]
    )
    assert absent == {
        "available": False,
        "should_clarify": None,
        "basis": "confidence_unavailable",
    }


class ContextRecordingAdapter:
    def __init__(self) -> None:
        self.context_lengths: list[int] = []

    def plan(
        self,
        case: Any,
        transcript: str,
        context: Sequence[Mapping[str, Any]],
    ) -> SemanticObservation:
        self.context_lengths.append(len(context))
        raw = case.observed["semantic"]
        return SemanticObservation(plan=raw, timings_ms={})


def test_multiturn_sequence_passes_prior_semantic_context() -> None:
    cases = [case for case in load_corpus(FIXTURE) if case.sequence_id == "light-follow-up"]
    adapter = ContextRecordingAdapter()
    report = (
        RegressionHarness(ReplayAsrAdapter(), adapter)
        .run(cases, corpus_root=FIXTURE.parent)
        .payload
    )
    assert adapter.context_lengths == [0, 1, 2]
    assert report["summary"]["reference_accuracy"] == 1.0


def test_baseline_comparison_reports_semantic_and_wer_regressions() -> None:
    baseline = {
        "summary": {
            "word_error_rate": 0.1,
            "task_family_accuracy": 1.0,
            "exact_argument_accuracy": 1.0,
        }
    }
    current = {
        "summary": {
            "word_error_rate": 0.2,
            "task_family_accuracy": 1.0,
            "exact_argument_accuracy": 0.8,
        }
    }
    comparison = compare_baseline(current, baseline)
    assert comparison["passed"] is False
    assert comparison["deltas"]["word_error_rate"] == 0.1
    assert len(comparison["regressions"]) == 2
