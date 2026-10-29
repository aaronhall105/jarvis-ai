import json
import sqlite3

from tools.developer_session_diagnostics import _tool_outcome


def test_real_read_and_search_results_are_successful():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    examples = [
        ("read_file", {"sha256": "a" * 64, "text": "source"}, "completed"),
        ("search", [], "completed"),
        ("search", [{"path": "example.py", "line": 1, "text": "match"}], "completed"),
        ("patch_file", {"ok": False, "code": "file_policy_rejected"}, "failed"),
        ("read_file", {"text": "missing sha"}, "failed"),
        ("search", [{"path": "example.py"}], "failed"),
    ]
    for tool, result, expected in examples:
        row = connection.execute(
            "SELECT ? AS tool, 'completed' AS state, ? AS result",
            (tool, json.dumps(result)),
        ).fetchone()
        assert _tool_outcome(row)[0] == expected
    connection.close()
