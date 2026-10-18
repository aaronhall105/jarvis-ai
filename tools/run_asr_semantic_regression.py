#!/usr/bin/env python3
"""Run public or private ASR-to-semantic plan regressions without writes."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "bridge"))

from app.asr_regression import (  # noqa: E402
    DEFAULT_PRIVATE_ROOT,
    CommandAdapter,
    CommandAsrAdapter,
    CommandSemanticAdapter,
    CorpusError,
    RegressionHarness,
    ReplayAsrAdapter,
    ReplaySemanticAdapter,
    assert_private_corpus_location,
    compare_baseline,
    human_summary,
    load_corpus,
    source_commit,
)


DEFAULT_FIXTURE = REPO_ROOT / "bridge" / "tests" / "fixtures" / "asr_regression_public.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score ASR output through semantic/tool planning without executing tools."
    )
    parser.add_argument("corpus", nargs="?", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--private", action="store_true", help="enforce private corpus location")
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    parser.add_argument("--case", action="append", default=[], help="run one case ID (repeatable)")
    parser.add_argument("--endpoint", choices=("PHONE", "WATCH", "VOICE_DEVICE"))
    parser.add_argument("--asr-command", help="local JSON stdin/stdout transcription command")
    parser.add_argument("--semantic-command", help="local JSON stdin/stdout plan-only command")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument(
        "--include-sensitive-transcripts",
        action="store_true",
        help="include transcript text in local output (never use for public artifacts)",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        corpus_path = args.corpus.expanduser().resolve()
        if args.private:
            assert_private_corpus_location(
                corpus_path,
                private_root=args.private_root,
                repo_root=REPO_ROOT,
            )
        cases = load_corpus(corpus_path)
        wanted = set(args.case)
        if wanted:
            cases = [case for case in cases if case.case_id in wanted]
            missing = wanted - {case.case_id for case in cases}
            if missing:
                raise CorpusError(f"unknown case IDs: {', '.join(sorted(missing))}")
        if args.endpoint:
            cases = [case for case in cases if case.metadata.get("endpoint") == args.endpoint]
        if not cases:
            raise CorpusError("filters selected no cases")

        if bool(args.asr_command) != bool(args.semantic_command):
            raise CorpusError("--asr-command and --semantic-command must be supplied together")
        if args.asr_command:
            asr_adapter = CommandAsrAdapter(CommandAdapter(shlex.split(args.asr_command)))
            semantic_adapter = CommandSemanticAdapter(
                CommandAdapter(shlex.split(args.semantic_command))
            )
        else:
            asr_adapter = ReplayAsrAdapter()
            semantic_adapter = ReplaySemanticAdapter()

        report = RegressionHarness(
            asr_adapter,
            semantic_adapter,
            include_sensitive=args.include_sensitive_transcripts,
        ).run(cases, corpus_root=corpus_path.parent, source_commit=source_commit(REPO_ROOT))
        payload = dict(report.payload)
        if args.baseline:
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
            payload["baseline_comparison"] = compare_baseline(payload, baseline)
        encoded = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        if args.json_output:
            args.json_output.write_text(encoded, encoding="utf-8")
        print(human_summary(payload))
        if args.json_output:
            print(f"JSON report: {args.json_output}")
        comparison = payload.get("baseline_comparison", {})
        return int(bool(payload["summary"]["failed"]) or comparison.get("passed") is False)
    except (CorpusError, OSError, json.JSONDecodeError) as exc:
        print(f"ASR semantic regression error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
