from __future__ import annotations

import sys
from pathlib import Path


OLD_V16_0_1 = '''        async def delayed_progress() -> None:
            nonlocal first_output_ms
            # A short, natural acknowledgement masks long STT/model/tool pauses,
            # but instant local replies are allowed to complete without filler.
            await asyncio.sleep(0.55)
            if first_answer_event.is_set():
                return
            if first_output_ms is None:
                first_output_ms = round((time.monotonic() - stream_started) * 1000)
            profile = tone_engine.analyse(request.text)
            await queue.put({
                "type": "progress",
                "message": tone_engine.progress_phrase(request.text, profile),
            })
'''

OLD_V16_0_2 = '''        async def delayed_progress() -> None:
            nonlocal first_output_ms
            # Show an interim thinking phrase only for requests that remain slow.
            # Fast deterministic commands complete without filler.
            profile = tone_engine.analyse(request.text)
            if not tone_engine.should_emit_progress(request.text, profile):
                return
            await asyncio.sleep(
                tone_engine.progress_delay_seconds(request.text, profile)
            )
            if first_answer_event.is_set():
                return
            if first_output_ms is None:
                first_output_ms = round((time.monotonic() - stream_started) * 1000)
            phrase = tone_engine.progress_phrase(request.text, profile).strip()
            if not phrase:
                return
            await queue.put({
                "type": "progress",
                "message": phrase,
                "presentation": "thinking",
            })
'''

NEW = '''        async def delayed_progress() -> None:
            nonlocal first_output_ms
            # Spoken progress is only useful for a real voice pipeline. Typed Assist
            # remains clean, and quick deterministic replies complete without filler.
            if not request.voice_mode:
                return
            profile = tone_engine.analyse(request.text)
            if not tone_engine.should_emit_progress(request.text, profile):
                return
            await asyncio.sleep(
                tone_engine.progress_delay_seconds(request.text, profile)
            )
            if first_answer_event.is_set():
                return
            phrase = tone_engine.progress_phrase(request.text, profile).strip()
            if not phrase:
                return
            if first_output_ms is None:
                first_output_ms = round((time.monotonic() - stream_started) * 1000)
            await queue.put({
                "type": "progress",
                "message": phrase,
                "presentation": "spoken_thinking",
            })
'''


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: patch_core_progress_v16_0_3.py PATH_TO_MAIN_PY", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    text = path.read_text()
    if NEW in text:
        print(f"[v16.0.3] Spoken adaptive progress patch already present in {path}")
        return 0
    for old in (OLD_V16_0_2, OLD_V16_0_1):
        if old in text:
            path.write_text(text.replace(old, NEW, 1))
            print(f"[v16.0.3] Patched spoken adaptive progress in {path}")
            return 0
    print(
        "[v16.0.3] Expected v16 streaming progress block was not found; "
        "refusing an unsafe partial patch.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
