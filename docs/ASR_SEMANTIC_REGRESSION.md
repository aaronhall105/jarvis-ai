# ASR → semantic/tool regression

This harness measures whether speech recognition changes what Jarvis understands
and would do. Word error rate is retained as a diagnostic, but the release-facing
scores are task family, semantic operation, grounded target, capability/tool,
exact arguments, critical tokens, clarification behaviour, and safety outcome.

## Current voice path audit

The Android phone captures PCM audio and sends it through the authenticated Core
Realtime WebSocket. A Watch session uses the same path through the phone bridge;
the authenticated `PHONE` or `WATCH` endpoint remains in turn metadata. Voice PE
uses its own authenticated client kind and stable satellite identity. Core sends
24 kHz PCM to OpenAI Realtime and configures `gpt-4o-transcribe` with server VAD.
The final `conversation.item.input_audio_transcription.completed` text enters the
normal `_execute_ai_request` and `AIEngine` path used by typed requests.

Core requests transcription token log probabilities. Today it retains their
geometric-mean probability as one transcript confidence value. It does **not**
currently receive or retain partial transcripts, word timestamps, alternatives,
or an N-best list. The harness never invents those signals: confidence-dependent
recommendations are reported unavailable when neither token nor aggregate
confidence was supplied by the adapter.

## Privacy and corpus location

Real household audio belongs outside Git by default:

```text
~/.local/share/jarvis-private-asr-regression/
  corpus.json
  audio/
    case-0001.wav
```

Set `JARVIS_PRIVATE_ASR_CORPUS` to move that root. A private run refuses a
manifest outside the configured root and refuses a private root inside the Git
repository. Defensive repository ignore rules cover local fallback directories
and `*.private-asr.json`, but the external root is the normal design.

Never put private audio or a private report in CI artifacts, bug reports, release
bundles, or commits. Reports omit audio paths and transcript text by default.
`--include-sensitive-transcripts` is an explicit local-only debugging option.

No private corpus was present when Stage B was implemented. Public fixtures are
sanitized, text-level replay observations; they test scoring and safety machinery
and make no claim about Aaron's accent.

## Corpus schema

The versioned JSON manifest stores cases with:

- `case_id`, optional relative `audio_path`, and optional `gold_transcript`;
- expected task family, semantic operation, target, scope/reference,
  capability/tool, arguments, clarification decision, and safety outcome;
- critical tokens with class, semantic field path, and expected value;
- endpoint, microphone, environment, speech style, and local notes metadata;
- optional `sequence_id` and `turn_index` for production-like follow-ups.

Critical classes are `PERSON`, `DEVICE`, `AREA`, `NUMBER`, `PERCENTAGE`,
`MONEY`, `DATE`, `TIME`, `DURATION`, `OPTION`, and `MODE`. New classes can be
added by extending the enum and schema validation rather than phrase routing.

The checked-in fixture at
`bridge/tests/fixtures/asr_regression_public.json` contains no audio or private
transcripts. It covers sanitized minimal pairs, endpoint types, read/write plans,
and a three-turn reference sequence.

## Plan-only safety boundary

The regression evaluator has no tool executor. Its semantic adapter protocol can
only return a plan observation. A command adapter receives:

```json
{"mode":"plan_only","allow_execution":false}
```

and any response claiming `execution_attempted` or `write_executed` is rejected.
The evaluator classifies the proposed plan as `WOULD_EXECUTE_SAFE`,
`WOULD_CLARIFY`, `WOULD_REJECT`, or `INVALID_PLAN`; it never calls Home
Assistant, mail, tasks, schedules, or another provider. Adapter programs used for
private evaluation must therefore expose a planning boundary, not the normal
execution endpoint.

## Running

Public sanitized fixtures:

```bash
/home/aaron/.venvs/jarvis-phase2/bin/python \
  tools/run_asr_semantic_regression.py \
  --json-output /tmp/jarvis-asr-public.json
```

One case or endpoint subset:

```bash
python tools/run_asr_semantic_regression.py --case percentage-thirty
python tools/run_asr_semantic_regression.py --endpoint WATCH
```

Private audio with local JSON stdin/stdout adapters:

```bash
python tools/run_asr_semantic_regression.py \
  ~/.local/share/jarvis-private-asr-regression/corpus.json \
  --private \
  --asr-command '/path/to/local-asr-adapter' \
  --semantic-command '/path/to/jarvis-plan-only-adapter' \
  --json-output ~/.local/share/jarvis-private-asr-regression/latest-report.json
```

The ASR adapter receives an absolute audio path and returns transcript, model,
available confidence, and latency. The semantic adapter receives the transcript,
endpoint, and preceding turns in the declared sequence. This keeps the corpus
usable with the current Realtime recognizer or a future provider without changing
the scoring model.

Use `--baseline LOCAL_REPORT.json` to report metric deltas and fail when WER
increases or semantic/tool/argument accuracy falls. Private baselines stay under
the private root.

## Confidence and clarification

The risk recommendation uses critical-token confidence when an adapter actually
provides it, otherwise the current aggregate transcript confidence. A low signal
only recommends clarification when a critical argument is present and the plan is
write-capable or its effect is unknown. A harmless read or a low-confidence
non-critical word does not automatically trigger confirmation. This diagnostic
does not alter production routing in Stage B.

## Timings

Reports collect ASR latency and any adapter-provided `understanding`, `planning`,
and `grounding` timings, each summarized at p50/p95. These measurements establish
the local baseline for later latency work; Stage B performs no speculation and
adds no runtime instrumentation to ordinary voice turns.

## Explicit capture only

Collect clips only through a consciously initiated phone/watch diagnostic flow or
an ordinary local recorder. Do not add background capture. Copy the resulting clip
into the private root, add its relative path to the private manifest, and stop the
recording immediately after the intended utterance. The repository intentionally
contains no always-listening collection helper.
