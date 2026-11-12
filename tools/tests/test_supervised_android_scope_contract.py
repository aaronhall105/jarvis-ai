"""Both languages execute the same immutable allow/deny fixture contract."""

import json
from pathlib import Path

from tools.supervised_development_scope import editable_path


def test_android_and_protected_worker_scope_vectors():
    vectors = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "android/jarvis-voice-client/app/src/test/resources/development_scope_vectors.json"
        ).read_text()
    )
    for verdict, paths in vectors.items():
        for path in paths:
            assert editable_path(path) is (verdict == "allowed"), path
