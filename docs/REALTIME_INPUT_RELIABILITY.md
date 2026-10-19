# Stage F — realtime input reliability

Starting production: `d7c16341d1dfd2ab093aa6c740f720c838e4d82f`.
Public release remains `19.0.0-alpha39`. Speculative reads remain OFF.

## Evidence and limits

The preserved local E.2 trace (`/tmp/jarvis-e2-evidence/trace.jsonl`) contains
76 events. Its first attempted utterance has only observed speech-start and
speech-stop events, for the same hashed provider item. The final observed event
was speech-stop at monotonic `755524.902818717`. The client waited 70 seconds
without a transcript or answer. The observer did **not** capture commits, item
creation, transcription failures, provider errors, or reader termination. Thus
the cause of that individual missing transcription remains unknown. The private
trace and report have been preserved, not committed.

Independent, reproducible product defects were found: no explicit transcription
failure handling, no bounded input timeout, and no item-level duplicate/late-final
admission fence. These are the scope of this correction; planner latency and
speculation operation alignment are not changed.

E.2's two successful transcripts completed 701–709 ms after speech-stop. Two
additional Stage F baseline OFF requests completed 458 and 783 ms after observed
speech-stop. Both used synthetic speech through the real authenticated realtime
PCM/provider path, not physical PHONE microphones. Neither reproduced the lost
turn. These small samples are not a provider latency SLA or accent certification.
Commit timestamps were not available in the old observer.

## Existing path and new ownership boundary

PHONE and WATCH authenticate separately identified sessions in
`RealtimeVoiceProxy.handle`. Their endpoint comes from authenticated session
metadata, not provider text. Voice Preview uses the existing `voice_pe` identity
and 16→24 kHz conversion. `_client_to_openai` forwards PCM using
`input_audio_buffer.append`; server VAD commits live speech. No second ASR,
provider switch, audio replay, or client protocol redesign is introduced.

`_openai_to_client` owns a provider-connection input tracker and watchdog.
`_consume_openai_events` correlates/fences input lifecycle events before calling
the existing event dispatcher. `_start_brain_turn` hands an accepted final to the
existing generation-fenced `_run_brain_turn` and ordinary authoritative Core
planner. The input terminal `delivered_to_core` means accepted/scheduled into
that Core task, not that planning, tool execution, or playback succeeded; existing
response/turn-ledger terminal handling continues to own those later outcomes.

The provider connection epoch, session, endpoint, provider `item_id`, and input
generation identify a turn. Assistant response generations remain independent:
normal finalization may advance response generation without invalidating the
input. A cancellation for old assistant playback only cancels input items up to
that old generation, preserving new barge-in speech.

OpenAI's [transcription contract](https://developers.openai.com/api/docs/guides/realtime-transcription)
requires correlation by item ID and does not guarantee completion order between
different speech turns. We therefore do not bind a late final to whichever turn
happens to be current.

## Events and outcomes

| Provider event | Input behavior |
|---|---|
| `input_audio_buffer.speech_started` | Own item, record time/generation; explicitly supersede undelivered prior input |
| `input_audio_buffer.speech_stopped` | Record stop; start bounded wait even if commit never arrives |
| `input_audio_buffer.committed` | Record commit; bound transcription wait |
| `conversation.item.created` / `conversation.item.added` | Record user audio item creation; ignore assistant/text-only items |
| `conversation.item.input_audio_transcription.delta` | Record first delta; existing bounded private partial processing stays optional |
| `conversation.item.input_audio_transcription.completed` | Claim final once; require known item, nonterminal ownership, unexpired deadline |
| `conversation.item.input_audio_transcription.failed` | Explicit `transcription_failed`, no Core command, no provider error body logged |
| `error` | Existing provider error/recovery path; uncorrelated errors are not assigned invented item ownership |
| Provider/client disconnect, replacement, or session cleanup | Terminalize pending inputs as `disconnected`; old epoch fenced |

Terminal outcomes are `delivered_to_core`, `no_speech`,
`empty_transcript_after_speech`, `transcription_failed`,
`transcription_timeout`, `cancelled`, `superseded`, `disconnected`,
`delivery_failed`, `suppressed` (existing Voice Preview/standard-mode policy),
and `handled_locally` (existing closure handling). Empty text never enters Core.
No delta is required for successful final delivery. A final for an unknown item
is counted as `unowned_final_discarded`, never assigned the newest generation.

Sequential completed user turns both deliver. If a new utterance begins while
an older one is still undelivered, latest-input ownership explicitly supersedes
the older input; its delayed final cannot interrupt the newer speech or become
the newer command. Existing wake-residue suppression is consequently fenced
earlier by item ownership in that overlapping case. The legitimate short-command
regression remains covered.

## Bounds, recovery, and queue pressure

The deadline is 30 seconds from the earliest observed speech-stop, commit,
audio-item creation, or first delta. Deltas never extend it. Speech with no stop
has a separate generous five-minute absolute bound. A one-second watchdog sweep
terminalizes idle items; event admission also checks expiry, so a final arriving
between sweeps cannot bypass it. This is a reliability deadline, not an ASR or
speculation latency optimization. No speculative TTL, threshold, eligibility,
fingerprint, or in-flight reuse policy changed.

Only a still-current, speech-detected failure/timeout uses the existing client
`error` surface to say it did not catch the request. No guessed text enters the
planner; no automatic audio retry or recovery TTS is started. Cancellation,
supersession, and disconnect do not produce stale recovery messages.

There is no new application event queue. The existing WebSocket queue applies
backpressure at 64 frames; the consumer does not evict critical events. Optional
delta-handler exceptions are isolated, counted, and cannot stop later final
delivery. Critical delivery exceptions terminalize the affected item and use the
existing provider recovery path without replay. Provider-loss cleanup fences
input/response state before awaiting task cancellation.

Up to 2,048 item records/tombstones are retained per provider connection. Capacity
exhaustion fails closed and renews the connection rather than forgetting dedup
history. Disconnect clears ownership; provider epochs prevent old socket events
from reaching replacement sessions. No new database or durable audio storage.

## Diagnostics and tests

`/api/realtime/voice/status.input_lifecycle` exposes fixed-category counts,
active input count, last terminal outcome, and timeout policy. Logs correlate
hashed session/item IDs, endpoint, provider epoch, generation, lifecycle stage,
and monotonic time. No new transcript/audio/error-body logs. First-delta and
terminal records are once per item; duplicate counts may increment on repeats.
Lifecycle telemetry exceptions cannot alter ownership.

`test_realtime_input_lifecycle.py` covers success with/without deltas, failure,
empty/no-speech distinction, duplicate and late finals, idle timeout, absolute
speech bound, cancellation and barge-in, supersession, sequential genuine turns,
disconnect at multiple phases, PHONE/WATCH isolation, capacity/tombstones,
diagnostic failure, delta-handler failure, and pressure followed by final/failure.
Existing realtime, proactive, Home, ASR, restore, and structural speculation
safety suites remain required gates. Physical PHONE/WATCH certification requires
hardware; no reinstall, data clearing, or authentication reset is authorized.
