#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8091"


def req(path, payload=None):
    body = None
    headers = {"Accept": "application/json"}
    method = "GET"
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
        method = "POST"
    try:
        with urlopen(
            Request(BASE + path, data=body, headers=headers, method=method), timeout=5
        ) as r:
            return json.load(r)
    except HTTPError as exc:
        print(exc.read().decode("utf-8", "replace"), file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        raise SystemExit(f"Voice ID service unavailable: {exc}")


def main():
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "status").casefold()
    if cmd in {"status", "health"}:
        print(json.dumps(req("/health"), indent=2))
        return
    if cmd == "list":
        speakers = req("/speakers").get("speakers", [])
        if not speakers:
            print("No voice profiles enrolled.")
            return
        for s in speakers:
            print(
                f"{s.get('display_name')} [{s.get('speaker_id')}] samples={s.get('sample_count')} source={s.get('source')}"
            )
        return
    if cmd in {"forget", "delete", "remove"}:
        if len(sys.argv) < 3:
            raise SystemExit("Usage: voice_id_admin.py forget <speaker_id>")
        speaker_id = sys.argv[2].strip().casefold().replace(" ", "_")
        print(json.dumps(req("/speakers/delete", {"speaker_id": speaker_id}), indent=2))
        return
    raise SystemExit("Usage: voice_id_admin.py [status|list|forget <speaker_id>]")


if __name__ == "__main__":
    main()
