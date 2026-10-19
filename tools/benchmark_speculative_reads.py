#!/usr/bin/env python3
"""Measure the coordinator's local reuse overhead with an inert read callback.

This is an architecture benchmark, not a claim about provider or household
latency. It performs no external calls and no writes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from collections.abc import Mapping
from typing import Any

from app.speculative_reads import (
    ProvisionalReadPlan,
    SemanticReadIdentity,
    SpeculativeReadCoordinator,
    ToolEffect,
    ToolEffectRegistry,
)


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * ratio)))
    return ordered[index]


async def run(iterations: int, read_ms: float, provisional_lead_ms: float) -> dict[str, Any]:
    async def inert_read(arguments: Mapping[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(read_ms / 1000)
        return {"tool": "inert_read", "arguments": dict(arguments), "result": {"ok": True}}

    registry = ToolEffectRegistry()
    registry.register_read(
        "inert_read",
        inert_read,
        effect=ToolEffect.PURE_LOCAL,
        provider="inert",
    )
    baseline: list[float] = []
    speculative: list[float] = []

    for generation in range(1, iterations + 1):
        started = time.perf_counter()
        await inert_read({"target": "synthetic"})
        baseline.append((time.perf_counter() - started) * 1000)

        identity = SemanticReadIdentity(
            session_id="benchmark",
            conversation_id="benchmark",
            generation=generation,
            task_family="LOCAL",
            semantic_operation="READ",
            tool_name="inert_read",
            arguments={"target": "synthetic"},
            provider_identity="inert",
        )
        coordinator = SpeculativeReadCoordinator(registry.read_projection(), enabled=True)
        await coordinator.start(
            ProvisionalReadPlan(
                identity=identity,
                input_version=1,
                effect=ToolEffect.PURE_LOCAL,
            )
        )
        await asyncio.sleep(provisional_lead_ms / 1000)
        final_started = time.perf_counter()
        await coordinator.resolve(identity, wait_for_in_flight=False)
        speculative.append((time.perf_counter() - final_started) * 1000)

    def summary(values: list[float]) -> dict[str, float]:
        return {
            "mean_ms": round(statistics.fmean(values), 3),
            "p50_ms": round(percentile(values, 0.50), 3),
            "p95_ms": round(percentile(values, 0.95), 3),
        }

    return {
        "kind": "synthetic_inert_read_coordinator_benchmark",
        "iterations": iterations,
        "configured_read_ms": read_ms,
        "provisional_lead_ms": provisional_lead_ms,
        "baseline_final_to_result": summary(baseline),
        "speculation_final_validation_to_result": summary(speculative),
        "limitations": (
            "No ASR, semantic model, provider network, answer generation, or audio playback is "
            "included; this measures only inert read overlap and coordinator reuse overhead."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--read-ms", type=float, default=50.0)
    parser.add_argument("--provisional-lead-ms", type=float, default=60.0)
    arguments = parser.parse_args()
    result = asyncio.run(
        run(
            max(1, arguments.iterations),
            max(0.0, arguments.read_ms),
            max(0.0, arguments.provisional_lead_ms),
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
