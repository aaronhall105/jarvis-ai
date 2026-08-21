#!/usr/bin/env python3
"""Deterministic, privacy-safe scoring for Jarvis recognition experiments."""

from __future__ import annotations

import argparse
import json
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def normalise(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text or "").casefold())


def edit_distance(left: list[str], right: list[str]) -> int:
    previous = list(range(len(right) + 1))
    for row, left_word in enumerate(left, 1):
        current = [row]
        for column, right_word in enumerate(right, 1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (left_word != right_word),
            ))
        previous = current
    return previous[-1]


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return round(ordered[index], 1)


@dataclass(frozen=True)
class Evaluation:
    summary: dict[str, Any]
    failures: tuple[dict[str, Any], ...]


def evaluate(rows: list[dict[str, Any]]) -> Evaluation:
    if not rows:
        raise ValueError("evaluation input is empty")
    total_words = 0
    total_errors = 0
    wake_successes = 0
    command_successes = 0
    interruption_successes = 0
    transcript_latencies: list[float] = []
    response_latencies: list[float] = []
    failures: list[dict[str, Any]] = []

    for index, row in enumerate(rows):
        expected = normalise(row.get("expected_text", ""))
        actual = normalise(row.get("transcript", ""))
        errors = edit_distance(expected, actual)
        total_words += max(1, len(expected))
        total_errors += errors
        woke = bool(row.get("wake_detected"))
        command_ok = bool(row.get("command_correct"))
        interruption_expected = bool(row.get("interruption_expected"))
        interruption_ok = bool(row.get("interruption_correct"))
        wake_successes += int(woke)
        command_successes += int(command_ok)
        if interruption_expected:
            interruption_successes += int(interruption_ok)
        for key, destination in (
            ("speech_end_to_transcript_ms", transcript_latencies),
            ("speech_end_to_first_audio_ms", response_latencies),
        ):
            value = row.get(key)
            if isinstance(value, (int, float)) and value >= 0:
                destination.append(float(value))
        if not woke or not command_ok or errors:
            failures.append({
                "case_id": str(row.get("case_id") or index),
                "room": str(row.get("room") or "unknown"),
                "condition": str(row.get("condition") or "unknown"),
                "word_errors": errors,
                "wake_detected": woke,
                "command_correct": command_ok,
            })

    interruption_cases = sum(bool(row.get("interruption_expected")) for row in rows)
    summary = {
        "cases": len(rows),
        "word_error_rate": round(total_errors / total_words, 4),
        "wake_success_rate": round(wake_successes / len(rows), 4),
        "command_success_rate": round(command_successes / len(rows), 4),
        "interruption_success_rate": (
            round(interruption_successes / interruption_cases, 4)
            if interruption_cases else None
        ),
        "speech_end_to_transcript_p50_ms": percentile(transcript_latencies, 0.50),
        "speech_end_to_transcript_p95_ms": percentile(transcript_latencies, 0.95),
        "speech_end_to_first_audio_p50_ms": percentile(response_latencies, 0.50),
        "speech_end_to_first_audio_p95_ms": percentile(response_latencies, 0.95),
        "failure_count": len(failures),
    }
    return Evaluation(summary=summary, failures=tuple(failures))


def regression_reasons(candidate: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    higher_is_better = ("wake_success_rate", "command_success_rate", "interruption_success_rate")
    lower_is_better = ("word_error_rate", "speech_end_to_transcript_p95_ms", "speech_end_to_first_audio_p95_ms")
    for key in higher_is_better:
        if candidate.get(key) is not None and baseline.get(key) is not None and candidate[key] < baseline[key]:
            reasons.append(f"{key} regressed from {baseline[key]} to {candidate[key]}")
    for key in lower_is_better:
        if candidate.get(key) is not None and baseline.get(key) is not None and candidate[key] > baseline[key]:
            reasons.append(f"{key} regressed from {baseline[key]} to {candidate[key]}")
    return reasons


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="JSON array or newline-delimited JSON evidence")
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    text = args.input.read_text(encoding="utf-8")
    rows = json.loads(text) if text.lstrip().startswith("[") else [json.loads(line) for line in text.splitlines() if line.strip()]
    result = evaluate(rows)
    payload: dict[str, Any] = {"summary": result.summary, "failures": result.failures}
    exit_code = 0
    if args.baseline:
        baseline_payload = json.loads(args.baseline.read_text(encoding="utf-8"))
        baseline = baseline_payload.get("summary", baseline_payload)
        reasons = regression_reasons(result.summary, baseline)
        payload["release_gate"] = {"passed": not reasons, "reasons": reasons}
        exit_code = int(bool(reasons))
    print(json.dumps(payload, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
