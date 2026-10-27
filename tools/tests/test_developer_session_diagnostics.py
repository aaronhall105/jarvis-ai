import json
import sqlite3

from tools.developer_session_diagnostics import load_recent_sessions, render_report


def build_database(path, *, include_calls=True):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE threads (id TEXT PRIMARY KEY, backend TEXT NOT NULL, state TEXT NOT NULL)"
        )
        if include_calls:
            connection.execute(
                "CREATE TABLE calls ("
                "id INTEGER PRIMARY KEY, thread TEXT NOT NULL, turn TEXT NOT NULL, call_id TEXT NOT NULL, "
                "signature TEXT NOT NULL, state TEXT NOT NULL, result TEXT, approval TEXT, expires REAL, "
                "tool TEXT, arguments TEXT, UNIQUE(thread,turn,call_id))"
            )


def insert_thread(connection, thread_id, payload):
    connection.execute(
        "INSERT INTO threads(id,backend,state) VALUES(?,?,?)",
        (thread_id, "api", json.dumps(payload) if not isinstance(payload, str) else payload),
    )


def insert_call(connection, identifier, thread_id, tool, state, result):
    connection.execute(
        "INSERT INTO calls(id,thread,turn,call_id,signature,state,result,tool,arguments) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (identifier, thread_id, "turn", f"call-{identifier}", "sig", state, result, tool, "{}"),
    )


def test_load_recent_sessions_summarises_completed_failed_uncertain_and_recoverable(tmp_path):
    database = tmp_path / "tasks.sqlite3"
    build_database(database)
    with sqlite3.connect(database) as connection:
        insert_thread(
            connection,
            "api_success",
            {"status": "completed", "createdAt": 1, "updatedAt": 10, "error": None},
        )
        insert_call(connection, 1, "api_success", "read_file", "completed", '{"ok": true}')
        insert_call(
            connection,
            2,
            "api_success",
            "search",
            "completed",
            '{"ok": false, "error": "no match"}',
        )
        insert_thread(
            connection,
            "api_failed",
            {"status": "failed", "createdAt": 2, "updatedAt": 20, "error": "Provider unavailable"},
        )
        insert_call(connection, 3, "api_failed", "patch_file", "uncertain", None)
        insert_call(connection, 4, "api_failed", "verify", "recoverable", None)

    report = load_recent_sessions(database)

    assert [session.session_id for session in report.sessions] == ["api_failed", "api_success"]
    failed, success = report.sessions
    assert failed.status == "failed"
    assert failed.failure_reason == "Provider unavailable"
    assert failed.completed_tools == 0
    assert failed.failed_tools == 0
    assert failed.uncertain_tools == 2
    assert failed.last_tool == "verify"
    assert failed.manual_reconciliation is True
    assert success.status == "completed"
    assert success.failure_reason is None
    assert success.completed_tools == 1
    assert success.failed_tools == 1
    assert success.uncertain_tools == 0
    assert success.last_tool == "search"
    assert success.manual_reconciliation is False


def test_load_recent_sessions_orders_by_recent_timestamps_not_thread_id(tmp_path):
    database = tmp_path / "tasks.sqlite3"
    build_database(database)
    with sqlite3.connect(database) as connection:
        insert_thread(
            connection,
            "zzz_old_id",
            {"status": "completed", "createdAt": 1, "updatedAt": 1, "error": None},
        )
        insert_thread(
            connection,
            "aaa_new_id",
            {"status": "completed", "createdAt": 2, "updatedAt": 100, "error": None},
        )
        for number in range(6):
            insert_thread(
                connection,
                f"mid_{number}",
                {
                    "status": "completed",
                    "createdAt": 10 + number,
                    "updatedAt": 10 + number,
                    "error": None,
                },
            )

    report = load_recent_sessions(database)

    assert len(report.sessions) == 5
    assert report.sessions[0].session_id == "aaa_new_id"
    assert "zzz_old_id" not in {session.session_id for session in report.sessions}


def test_load_recent_sessions_handles_missing_database(tmp_path):
    report = load_recent_sessions(tmp_path / "missing.sqlite3")
    assert report.sessions == ()
    assert report.warning == "Database not found"


def test_load_recent_sessions_handles_corrupted_database(tmp_path):
    database = tmp_path / "tasks.sqlite3"
    database.write_bytes(b"not a sqlite database")

    report = load_recent_sessions(database)

    assert report.sessions == ()
    assert report.warning is not None
    assert "Could not" in report.warning


def test_load_recent_sessions_handles_malformed_records_safely(tmp_path):
    database = tmp_path / "tasks.sqlite3"
    build_database(database)
    with sqlite3.connect(database) as connection:
        insert_thread(connection, "api_bad_json", "not-json")
        insert_thread(
            connection,
            "api_bad_fields",
            {"status": "mystery", "createdAt": "later", "updatedAt": None, "error": ["hidden"]},
        )
        insert_call(connection, 1, "api_bad_json", "repository", "completed", "not-json")
        insert_call(connection, 2, "api_bad_fields", "pr_status", "pending", None)

    report = load_recent_sessions(database)
    rendered = render_report(report)
    sessions = {session.session_id: session for session in report.sessions}

    assert report.warning is None
    assert len(report.sessions) == 2
    assert sessions["api_bad_fields"].uncertain_tools == 1
    assert sessions["api_bad_fields"].manual_reconciliation is False
    assert sessions["api_bad_fields"].warnings
    assert sessions["api_bad_json"].failed_tools == 1
    assert "Malformed thread state JSON" in rendered
    assert "Unexpected session status: mystery" in rendered


def test_load_recent_sessions_masks_sensitive_failure_reasons(tmp_path):
    database = tmp_path / "sensitive tasks.sqlite3"
    build_database(database)
    sensitive_error = (
        "Authorization: Bearer sk-secret-token api_key=abcdef123456 "
        'https://private.example/error {"detail":"hidden"}'
    )
    with sqlite3.connect(database) as connection:
        insert_thread(
            connection,
            "api_sensitive",
            {"status": "failed", "createdAt": 1, "updatedAt": 2, "error": sensitive_error},
        )

    report = load_recent_sessions(database)
    rendered = render_report(report)

    assert report.sessions[0].failure_reason == "Saved failure details withheld for privacy"
    assert "Bearer sk-secret-token" not in rendered
    assert "api_key=abcdef123456" not in rendered
    assert "private.example" not in rendered
    assert '"detail":"hidden"' not in rendered


def test_load_recent_sessions_handles_missing_calls_table_and_unusual_path(tmp_path):
    directory = tmp_path / "odd path ü"
    directory.mkdir()
    database = directory / "tasks sqlite3.db"
    build_database(database, include_calls=False)
    with sqlite3.connect(database) as connection:
        insert_thread(
            connection,
            "api_session",
            {"status": "running", "createdAt": 1, "updatedAt": 2, "error": None},
        )

    report = load_recent_sessions(database)

    assert report.warning is None
    assert len(report.sessions) == 1
    assert report.sessions[0].warnings == ("Could not read calls table",)


def test_load_recent_sessions_treats_pending_and_executing_as_uncertain_without_retries(tmp_path):
    database = tmp_path / "tasks.sqlite3"
    build_database(database)
    with sqlite3.connect(database) as connection:
        insert_thread(
            connection,
            "api_pending",
            {"status": "running", "createdAt": 1, "updatedAt": 3, "error": None},
        )
        insert_call(connection, 1, "api_pending", "repository", "pending", None)
        insert_call(connection, 2, "api_pending", "verify", "executing", None)
    before = database.read_bytes()

    report = load_recent_sessions(database)
    rendered = render_report(report)
    after = database.read_bytes()

    assert before == after
    assert report.sessions[0].uncertain_tools == 2
    assert report.sessions[0].manual_reconciliation is False
    assert report.sessions[0].last_tool == "verify"
    assert "repository" not in rendered


def test_render_report_is_read_only_summary_without_private_contents_and_db_is_unchanged(tmp_path):
    database = tmp_path / "tasks.sqlite3"
    build_database(database)
    with sqlite3.connect(database) as connection:
        insert_thread(
            connection,
            "api_session",
            {
                "status": "completed",
                "createdAt": 1,
                "updatedAt": 2,
                "error": None,
                "goal": "secret goal",
            },
        )
        insert_call(
            connection,
            1,
            "api_session",
            "read_file",
            "completed",
            '{"ok": true, "text": "private source", "token": "secret-token"}',
        )
    before = database.read_bytes()

    rendered = render_report(load_recent_sessions(database))
    after = database.read_bytes()

    assert before == after
    assert "secret goal" not in rendered
    assert "private source" not in rendered
    assert "secret-token" not in rendered
    assert "read_file" in rendered
