"""Exercise actual test owners: cleanup must not depend on process exit or GC."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="Linux descriptor accounting")
def test_gateway_test_owners_release_journals(tmp_path):
    plugin = tmp_path / "resource_probe.py"
    plugin.write_text("""
import os
from pathlib import Path
import pytest

@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_protocol(item, nextitem):
    yield
    root = os.environ['RESOURCE_PROBE_ROOT']
    leaked = 0
    for fd in Path('/proc/self/fd').iterdir():
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if target.startswith(root + '/'):
            leaked += 1
    assert leaked == 0, 'Test owner retained a journal or lock descriptor after teardown'
""")
    root = Path(__file__).resolve().parents[2]
    environment = {
        key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL"}
    }
    environment.update(
        HOME=str(tmp_path),
        TMPDIR=str(tmp_path),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=os.pathsep.join((str(tmp_path), str(root / "bridge"), str(root))),
        RESOURCE_PROBE_ROOT=str(tmp_path / "cases"),
        JARVIS_DEVELOPER_STATE_DIR=str(tmp_path / "unused-runtime"),
        JARVIS_DEVELOPER_API_KEY_FILE=str(tmp_path / "missing-key"),
    )
    cases = [
        "developer_gateway/tests/test_api_backend.py::test_thread_stream_final_and_stateful_continuation",
        "developer_gateway/tests/test_api_backend.py::test_missing_credential_fails_closed",
        "developer_gateway/tests/test_api_backend.py::test_restart_marks_uncertain_and_preserves_completed",
        "developer_gateway/tests/test_tools_isolation.py::test_real_isolated_worktree_patch_diff_and_replay",
        "developer_gateway/tests/test_tools_isolation.py::test_approval_cannot_cross_threads_or_turns",
        "developer_gateway/tests/test_tools_isolation.py::test_concurrent_threads_have_independent_jobs",
        "tools/tests/test_developer_session_diagnostics.py",
    ]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "resource_probe",
            "--basetemp",
            str(tmp_path / "cases"),
            *cases,
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
