"""Privacy-safe ASR-to-semantic regression scoring.

The evaluator deliberately has no tool executor.  It consumes plan-only
observations from an ASR/planner adapter and reports what Jarvis *would* do.
That boundary makes external writes impossible from this module while keeping
ASR providers and semantic planners replaceable for later comparisons.
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
import subprocess
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


SCHEMA_VERSION = 1
DEFAULT_PRIVATE_ROOT = Path(
    os.getenv(
        "JARVIS_PRIVATE_ASR_CORPUS",
        Path.home() / ".local" / "share" / "jarvis-private-asr-regression",
    )
).expanduser()


class CorpusError(ValueError):
    """Raised when a corpus or adapter violates the evaluation contract."""


class CriticalTokenClass(str, Enum):
    PERSON = "PERSON"
    DEVICE = "DEVICE"
    AREA = "AREA"
    NUMBER = "NUMBER"
    PERCENTAGE = "PERCENTAGE"
    MONEY = "MONEY"
    DATE = "DATE"
    TIME = "TIME"
    DURATION = "DURATION"
    OPTION = "OPTION"
    MODE = "MODE"


class ToolEffect(str, Enum):
    PURE_LOCAL = "PURE_LOCAL"
    READ_ONLY_EXTERNAL = "READ_ONLY_EXTERNAL"
    WRITE_REVERSIBLE = "WRITE_REVERSIBLE"
    WRITE = "WRITE"
    DESTRUCTIVE = "DESTRUCTIVE"
    UNKNOWN = "UNKNOWN"

    @property
    def is_write(self) -> bool:
        return self in {
            ToolEffect.WRITE_REVERSIBLE,
            ToolEffect.WRITE,
            ToolEffect.DESTRUCTIVE,
        }


class SafetyOutcome(str, Enum):
    WOULD_EXECUTE_SAFE = "WOULD_EXECUTE_SAFE"
    WOULD_CLARIFY = "WOULD_CLARIFY"
    WOULD_REJECT = "WOULD_REJECT"
    INVALID_PLAN = "INVALID_PLAN"


@dataclass(frozen=True, slots=True)
class CriticalToken:
    token_class: CriticalTokenClass
    field: str
    expected_value: Any


@dataclass(frozen=True, slots=True)
class RegressionCase:
    case_id: str
    audio_path: str | None
    gold_transcript: str | None
    expected: Mapping[str, Any]
    critical_tokens: tuple[CriticalToken, ...]
    metadata: Mapping[str, Any]
    observed: Mapping[str, Any]
    sequence_id: str | None = None
    turn_index: int = 0


@dataclass(frozen=True, slots=True)
class AsrObservation:
    transcript: str
    model: str | None = None
    confidence: float | None = None
    latency_ms: float | None = None
    token_confidences: Mapping[str, float] | None = None


@dataclass(frozen=True, slots=True)
class SemanticObservation:
    plan: Mapping[str, Any]
    timings_ms: Mapping[str, float]


class AsrAdapter(Protocol):
    """Plan-only ASR adapter; it cannot receive any semantic tool executor."""

    def transcribe(self, case: RegressionCase, audio_path: Path | None) -> AsrObservation: ...


class SemanticPlanAdapter(Protocol):
    """Produces a semantic observation without executing the proposed plan."""

    def plan(
        self,
        case: RegressionCase,
        transcript: str,
        context: Sequence[Mapping[str, Any]],
    ) -> SemanticObservation: ...


def _words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", str(value or "").casefold())


def _edit_distance(left: Sequence[str], right: Sequence[str]) -> int:
    previous = list(range(len(right) + 1))
    for row, left_word in enumerate(left, 1):
        current = [row]
        for column, right_word in enumerate(right, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + int(left_word != right_word),
                )
            )
        previous = current
    return previous[-1]


def word_error_counts(expected: str, actual: str) -> tuple[int, int]:
    """Return edit count and reference-word count for corpus-level WER."""

    reference = _words(expected)
    hypothesis = _words(actual)
    return _edit_distance(reference, hypothesis), len(reference)


def _normalise_scalar(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.casefold().split())
    if isinstance(value, float) and math.isfinite(value):
        return round(value, 6)
    return value


def _equivalent(expected: Any, actual: Any) -> bool:
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        return all(
            key in actual and _equivalent(value, actual[key]) for key, value in expected.items()
        )
    if isinstance(expected, Sequence) and not isinstance(expected, (str, bytes)):
        if not isinstance(actual, Sequence) or isinstance(actual, (str, bytes)):
            return False
        return len(expected) == len(actual) and all(
            _equivalent(left, right) for left, right in zip(expected, actual)
        )
    return _normalise_scalar(expected) == _normalise_scalar(actual)


def _field(payload: Mapping[str, Any], dotted_path: str) -> Any:
    current: Any = payload
    for part in dotted_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _bounded_float(value: Any, *, minimum: float = 0.0) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        return None
    return result


def _required_text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise CorpusError(f"{field} is required")
    return result


def parse_case(payload: Mapping[str, Any]) -> RegressionCase:
    case_id = _required_text(payload.get("case_id"), "case_id")
    expected = payload.get("expected")
    if not isinstance(expected, Mapping):
        raise CorpusError(f"{case_id}: expected must be an object")
    observed = payload.get("observed", {})
    if not isinstance(observed, Mapping):
        raise CorpusError(f"{case_id}: observed must be an object")
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise CorpusError(f"{case_id}: metadata must be an object")
    tokens: list[CriticalToken] = []
    raw_tokens = payload.get("critical_tokens", [])
    if not isinstance(raw_tokens, list):
        raise CorpusError(f"{case_id}: critical_tokens must be an array")
    for index, token in enumerate(raw_tokens):
        if not isinstance(token, Mapping):
            raise CorpusError(f"{case_id}: critical token {index} must be an object")
        try:
            token_class = CriticalTokenClass(str(token.get("class") or "").upper())
        except ValueError as exc:
            raise CorpusError(f"{case_id}: unsupported critical token class") from exc
        tokens.append(
            CriticalToken(
                token_class=token_class,
                field=_required_text(token.get("field"), "critical token field"),
                expected_value=token.get("expected_value"),
            )
        )
    turn_index = payload.get("turn_index", 0)
    if not isinstance(turn_index, int) or turn_index < 0:
        raise CorpusError(f"{case_id}: turn_index must be a non-negative integer")
    return RegressionCase(
        case_id=case_id,
        audio_path=(str(payload["audio_path"]).strip() if payload.get("audio_path") else None),
        gold_transcript=(
            str(payload["gold_transcript"]).strip() if payload.get("gold_transcript") else None
        ),
        expected=dict(expected),
        critical_tokens=tuple(tokens),
        metadata=dict(metadata),
        observed=dict(observed),
        sequence_id=(str(payload["sequence_id"]).strip() if payload.get("sequence_id") else None),
        turn_index=turn_index,
    )


def load_corpus(path: Path) -> list[RegressionCase]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusError(f"could not read corpus: {path}") from exc
    if not isinstance(payload, Mapping) or payload.get("schema_version") != SCHEMA_VERSION:
        raise CorpusError(f"corpus must declare schema_version={SCHEMA_VERSION}")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise CorpusError("corpus cases must be a non-empty array")
    cases = [parse_case(item) for item in raw_cases if isinstance(item, Mapping)]
    if len(cases) != len(raw_cases):
        raise CorpusError("every corpus case must be an object")
    if len({case.case_id for case in cases}) != len(cases):
        raise CorpusError("case_id values must be unique")
    sequence_turns: set[tuple[str, int]] = set()
    for case in cases:
        if case.sequence_id:
            key = (case.sequence_id, case.turn_index)
            if key in sequence_turns:
                raise CorpusError(f"duplicate sequence turn: {key}")
            sequence_turns.add(key)
    return sorted(cases, key=lambda case: (case.sequence_id or case.case_id, case.turn_index))


def assert_private_corpus_location(path: Path, *, private_root: Path, repo_root: Path) -> None:
    """Require private corpora to be below an out-of-repository private root."""

    resolved_path = path.expanduser().resolve()
    resolved_root = private_root.expanduser().resolve()
    resolved_repo = repo_root.expanduser().resolve()
    try:
        resolved_root.relative_to(resolved_repo)
    except ValueError:
        pass
    else:
        raise CorpusError("private corpus root must be outside the Git repository")
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise CorpusError(
            "private corpus manifest must be inside the configured private root"
        ) from exc


class ReplayAsrAdapter:
    """Read sanitized/precomputed ASR observations from a corpus fixture."""

    def transcribe(self, case: RegressionCase, audio_path: Path | None) -> AsrObservation:
        raw = case.observed.get("asr")
        if not isinstance(raw, Mapping):
            raise CorpusError(f"{case.case_id}: observed.asr is required for replay")
        confidence = raw.get("confidence")
        if confidence is not None and (
            not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1
        ):
            raise CorpusError(f"{case.case_id}: ASR confidence must be between zero and one")
        token_confidences = raw.get("token_confidences")
        return AsrObservation(
            transcript=str(raw.get("transcript") or ""),
            model=(str(raw.get("model") or "").strip() or None),
            confidence=(float(confidence) if confidence is not None else None),
            latency_ms=_bounded_float(raw.get("latency_ms")),
            token_confidences=(
                {str(key): float(value) for key, value in token_confidences.items()}
                if isinstance(token_confidences, Mapping)
                else None
            ),
        )


class ReplaySemanticAdapter:
    """Read sanitized/precomputed semantic observations from a corpus fixture."""

    def plan(
        self,
        case: RegressionCase,
        transcript: str,
        context: Sequence[Mapping[str, Any]],
    ) -> SemanticObservation:
        raw = case.observed.get("semantic")
        if not isinstance(raw, Mapping):
            raise CorpusError(f"{case.case_id}: observed.semantic is required for replay")
        timings = raw.get("timings_ms", {})
        return SemanticObservation(
            plan=dict(raw),
            timings_ms={
                str(key): float(value)
                for key, value in timings.items()
                if _bounded_float(value) is not None
            }
            if isinstance(timings, Mapping)
            else {},
        )


class CommandAdapter:
    """Local process adapter using JSON stdin/stdout and no shell expansion."""

    def __init__(self, argv: Sequence[str], *, timeout_seconds: float = 120.0) -> None:
        if not argv:
            raise CorpusError("adapter command cannot be empty")
        self.argv = tuple(argv)
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 600.0))

    def call(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        completed = subprocess.run(
            self.argv,
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            check=False,
            timeout=self.timeout_seconds,
        )
        if completed.returncode != 0:
            raise CorpusError(
                f"adapter failed with exit {completed.returncode}: {completed.stderr.strip()[:300]}"
            )
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise CorpusError("adapter did not return one JSON object") from exc
        if not isinstance(result, Mapping):
            raise CorpusError("adapter result must be an object")
        return result


class CommandAsrAdapter:
    def __init__(self, command: CommandAdapter) -> None:
        self.command = command

    def transcribe(self, case: RegressionCase, audio_path: Path | None) -> AsrObservation:
        if audio_path is None:
            raise CorpusError(f"{case.case_id}: audio is required by the ASR command adapter")
        started = time.monotonic()
        raw = self.command.call(
            {
                "mode": "transcribe_only",
                "case_id": case.case_id,
                "audio_path": str(audio_path),
                "endpoint": case.metadata.get("endpoint"),
            }
        )
        latency = _bounded_float(raw.get("latency_ms"))
        return AsrObservation(
            transcript=str(raw.get("transcript") or ""),
            model=(str(raw.get("model") or "").strip() or None),
            confidence=(
                float(raw["confidence"])
                if isinstance(raw.get("confidence"), (int, float))
                else None
            ),
            latency_ms=latency or round((time.monotonic() - started) * 1000, 3),
            token_confidences=(
                {str(key): float(value) for key, value in raw["token_confidences"].items()}
                if isinstance(raw.get("token_confidences"), Mapping)
                else None
            ),
        )


class CommandSemanticAdapter:
    def __init__(self, command: CommandAdapter) -> None:
        self.command = command

    def plan(
        self,
        case: RegressionCase,
        transcript: str,
        context: Sequence[Mapping[str, Any]],
    ) -> SemanticObservation:
        started = time.monotonic()
        raw = self.command.call(
            {
                "mode": "plan_only",
                "allow_execution": False,
                "case_id": case.case_id,
                "transcript": transcript,
                "endpoint": case.metadata.get("endpoint"),
                "conversation": list(context),
            }
        )
        timings = raw.get("timings_ms", {})
        measured = round((time.monotonic() - started) * 1000, 3)
        clean_timings = (
            {
                str(key): float(value)
                for key, value in timings.items()
                if _bounded_float(value) is not None
            }
            if isinstance(timings, Mapping)
            else {}
        )
        clean_timings.setdefault("semantic_adapter", measured)
        return SemanticObservation(plan=dict(raw), timings_ms=clean_timings)


def safety_outcome(plan: Mapping[str, Any]) -> SafetyOutcome:
    """Classify a plan without possessing any execution mechanism."""

    if plan.get("execution_attempted") is True or plan.get("write_executed") is True:
        raise CorpusError("plan-only adapter attempted execution")
    if plan.get("valid") is False:
        return SafetyOutcome.INVALID_PLAN
    if plan.get("clarification_required") is True:
        return SafetyOutcome.WOULD_CLARIFY
    if plan.get("rejected") is True:
        return SafetyOutcome.WOULD_REJECT
    try:
        effect = ToolEffect(str(plan.get("effect") or "UNKNOWN").upper())
    except ValueError:
        return SafetyOutcome.INVALID_PLAN
    if effect is ToolEffect.UNKNOWN:
        return SafetyOutcome.INVALID_PLAN
    return SafetyOutcome.WOULD_EXECUTE_SAFE


def clarification_risk_recommendation(
    asr: AsrObservation,
    plan: Mapping[str, Any],
    critical_tokens: Sequence[CriticalToken],
    *,
    confidence_threshold: float = 0.55,
) -> dict[str, Any]:
    """Use only real confidence evidence; never manufacture missing certainty."""

    critical_fields = {token.field for token in critical_tokens}
    if not critical_fields:
        return {
            "available": asr.confidence is not None,
            "should_clarify": False,
            "basis": "no_critical_argument",
        }
    confidence_values = [
        value
        for field, value in (asr.token_confidences or {}).items()
        if field in critical_fields and isinstance(value, (int, float))
    ]
    confidence: float | None
    basis: str
    if confidence_values:
        confidence = min(float(value) for value in confidence_values)
        basis = "critical_token_confidence"
    elif asr.confidence is not None:
        confidence = asr.confidence
        basis = "aggregate_transcript_confidence"
    else:
        return {"available": False, "should_clarify": None, "basis": "confidence_unavailable"}
    try:
        effect = ToolEffect(str(plan.get("effect") or "UNKNOWN").upper())
    except ValueError:
        effect = ToolEffect.UNKNOWN
    high_risk = effect.is_write or effect is ToolEffect.UNKNOWN
    return {
        "available": True,
        "should_clarify": bool(high_risk and confidence < confidence_threshold),
        "basis": basis,
        "confidence": round(confidence, 4),
        "threshold": confidence_threshold,
        "high_risk": high_risk,
    }


def _metric_match(expected: Mapping[str, Any], actual: Mapping[str, Any], key: str) -> bool | None:
    if key not in expected:
        return None
    return _equivalent(expected[key], actual.get(key))


def _safe_failure_value(value: Any, *, include_sensitive: bool) -> Any:
    if include_sensitive:
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _safe_failure_value(item, include_sensitive=False)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_safe_failure_value(item, include_sensitive=False) for item in value]
    return "[redacted]"


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return round(ordered[index], 3)


@dataclass(frozen=True, slots=True)
class RegressionReport:
    payload: Mapping[str, Any]


class RegressionHarness:
    def __init__(
        self,
        asr_adapter: AsrAdapter,
        semantic_adapter: SemanticPlanAdapter,
        *,
        include_sensitive: bool = False,
    ) -> None:
        self.asr_adapter = asr_adapter
        self.semantic_adapter = semantic_adapter
        self.include_sensitive = include_sensitive

    def run(
        self,
        cases: Sequence[RegressionCase],
        *,
        corpus_root: Path,
        source_commit: str | None = None,
    ) -> RegressionReport:
        if not cases:
            raise CorpusError("no cases selected")
        contexts: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        counters: dict[str, list[bool]] = defaultdict(list)
        critical: dict[str, list[bool]] = defaultdict(list)
        timing: dict[str, list[float]] = defaultdict(list)
        failures: list[dict[str, Any]] = []
        case_results: list[dict[str, Any]] = []
        wer_errors = 0
        wer_words = 0
        models: Counter[str] = Counter()

        for case in cases:
            audio_path = (corpus_root / case.audio_path).resolve() if case.audio_path else None
            if audio_path is not None and not audio_path.is_file():
                raise CorpusError(f"{case.case_id}: audio file is missing")
            asr_started = time.monotonic()
            asr = self.asr_adapter.transcribe(case, audio_path)
            asr_elapsed = round((time.monotonic() - asr_started) * 1000, 3)
            timing["asr"].append(asr.latency_ms if asr.latency_ms is not None else asr_elapsed)
            if asr.model:
                models[asr.model] += 1
            if case.gold_transcript is not None:
                errors, words = word_error_counts(case.gold_transcript, asr.transcript)
                wer_errors += errors
                wer_words += words
                case_wer: float | None = round(errors / max(1, words), 6)
            else:
                case_wer = None

            context = contexts[case.sequence_id] if case.sequence_id else []
            semantic_started = time.monotonic()
            observation = self.semantic_adapter.plan(case, asr.transcript, tuple(context))
            semantic_elapsed = round((time.monotonic() - semantic_started) * 1000, 3)
            actual = dict(observation.plan)
            for key, value in observation.timings_ms.items():
                timing[key].append(float(value))
            if not observation.timings_ms:
                timing["semantic"].append(semantic_elapsed)
            outcome = safety_outcome(actual)
            actual_with_outcome = {**actual, "safety_outcome": outcome.value}

            layer_keys = (
                "task_family",
                "semantic_operation",
                "grounded_target",
                "capability",
                "tool",
                "scope",
                "reference_dependency",
                "arguments",
                "clarification_required",
                "safety_outcome",
            )
            layers = {
                key: _metric_match(case.expected, actual_with_outcome, key) for key in layer_keys
            }
            metric_aliases = {
                "task_family": "task_family_accuracy",
                "semantic_operation": "semantic_operation_accuracy",
                "grounded_target": "grounded_target_accuracy",
                "capability": "capability_accuracy",
                "tool": "tool_accuracy",
                "arguments": "exact_argument_accuracy",
                "clarification_required": "clarification_accuracy",
                "reference_dependency": "reference_accuracy",
                "safety_outcome": "safety_accuracy",
            }
            for key, metric in metric_aliases.items():
                if layers[key] is not None:
                    counters[metric].append(bool(layers[key]))

            token_results: list[dict[str, Any]] = []
            for token in case.critical_tokens:
                actual_value = _field(actual_with_outcome, token.field)
                matched = _equivalent(token.expected_value, actual_value)
                critical[token.token_class.value].append(matched)
                safe_expected = _safe_failure_value(
                    token.expected_value,
                    include_sensitive=self.include_sensitive,
                )
                safe_actual = _safe_failure_value(
                    actual_value,
                    include_sensitive=self.include_sensitive,
                )
                token_results.append(
                    {
                        "class": token.token_class.value,
                        "field": token.field,
                        "matched": matched,
                        "expected": safe_expected,
                        "actual": safe_actual,
                    }
                )

            risk = clarification_risk_recommendation(asr, actual, case.critical_tokens)
            passed = all(value is not False for value in layers.values()) and all(
                item["matched"] for item in token_results
            )
            result = {
                "case_id": case.case_id,
                "sequence_id": case.sequence_id,
                "turn_index": case.turn_index,
                "endpoint": case.metadata.get("endpoint"),
                "word_error_rate": case_wer,
                "layers": layers,
                "critical_tokens": token_results,
                "safety_outcome": outcome.value,
                "clarification_risk": risk,
                "passed": passed,
            }
            if self.include_sensitive:
                result["transcript"] = asr.transcript
                result["gold_transcript"] = case.gold_transcript
            case_results.append(result)
            if not passed:
                failures.append(result)

            if case.sequence_id:
                context.append(
                    {
                        "turn_index": case.turn_index,
                        "transcript": asr.transcript,
                        "semantic_plan": actual,
                    }
                )

        def rate(values: Sequence[bool]) -> float | None:
            return round(sum(values) / len(values), 6) if values else None

        timing_summary = {
            key: {
                "p50_ms": _percentile(values, 0.50),
                "p95_ms": _percentile(values, 0.95),
            }
            for key, values in sorted(timing.items())
        }
        summary = {
            "case_count": len(cases),
            "passed": len(cases) - len(failures),
            "failed": len(failures),
            "word_error_rate": round(wer_errors / wer_words, 6) if wer_words else None,
            **{key: rate(values) for key, values in sorted(counters.items())},
            "critical_token_accuracy": (
                rate([item for values in critical.values() for item in values])
                if critical
                else None
            ),
            "critical_token_accuracy_by_class": {
                token_class.value: rate(critical.get(token_class.value, []))
                for token_class in CriticalTokenClass
            },
            "timings": timing_summary,
        }
        payload = {
            "schema_version": SCHEMA_VERSION,
            "run_id": f"asr-semantic-{time.time_ns()}",
            "source_commit": source_commit,
            "models": dict(models),
            "privacy": {
                "audio_embedded": False,
                "transcripts_included": self.include_sensitive,
                "private_corpus_uploaded": False,
            },
            "summary": summary,
            "failures": failures,
            "cases": case_results,
        }
        return RegressionReport(payload=payload)


def compare_baseline(current: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, Any]:
    current_summary = current.get("summary", current)
    baseline_summary = baseline.get("summary", baseline)
    if not isinstance(current_summary, Mapping) or not isinstance(baseline_summary, Mapping):
        raise CorpusError("baseline and current reports need summary objects")
    metric_names = {
        "word_error_rate": False,
        "task_family_accuracy": True,
        "semantic_operation_accuracy": True,
        "grounded_target_accuracy": True,
        "capability_accuracy": True,
        "tool_accuracy": True,
        "exact_argument_accuracy": True,
        "clarification_accuracy": True,
        "critical_token_accuracy": True,
    }
    deltas: dict[str, float] = {}
    regressions: list[str] = []
    for metric, higher_is_better in metric_names.items():
        current_value = current_summary.get(metric)
        baseline_value = baseline_summary.get(metric)
        if not isinstance(current_value, (int, float)) or not isinstance(
            baseline_value, (int, float)
        ):
            continue
        delta = round(float(current_value) - float(baseline_value), 6)
        deltas[metric] = delta
        if (higher_is_better and delta < 0) or (not higher_is_better and delta > 0):
            regressions.append(f"{metric}: {baseline_value} -> {current_value}")
    return {"passed": not regressions, "deltas": deltas, "regressions": regressions}


def source_commit(repo_root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def human_summary(report: Mapping[str, Any]) -> str:
    summary = report.get("summary", {})
    if not isinstance(summary, Mapping):
        return "Invalid ASR semantic regression report"
    lines = [
        f"Cases: {summary.get('passed', 0)}/{summary.get('case_count', 0)} passed",
        f"WER: {summary.get('word_error_rate')}",
        f"Task family: {summary.get('task_family_accuracy')}",
        f"Semantic operation: {summary.get('semantic_operation_accuracy')}",
        f"Grounded target: {summary.get('grounded_target_accuracy')}",
        f"Capability/tool: {summary.get('capability_accuracy')} / {summary.get('tool_accuracy')}",
        f"Exact arguments: {summary.get('exact_argument_accuracy')}",
        f"Clarification: {summary.get('clarification_accuracy')}",
        f"Critical tokens: {summary.get('critical_token_accuracy')}",
    ]
    return "\n".join(lines)
