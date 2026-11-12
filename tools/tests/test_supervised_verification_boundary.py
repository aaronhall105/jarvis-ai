"""Reproduce locked-proc inheritance with a disposable non-production UID.

Docker's default masked/read-only proc paths reproduce the relevant kernel
restriction; they are never unmasked. Its additional namespace seccomp filter
is disabled only in this disposable container, because the reviewed systemd
service permits these namespace calls. No host policy or service is changed.
"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(
    os.environ.get("JARVIS_PROCESS_DOCKER_TEST") != "1",
    reason="explicit disposable masked-proc container rehearsal required",
)
def test_locked_proc_parent_supports_credential_free_verifier_without_hardening_changes():
    source = Path(__file__).resolve().parents[2]
    prefix = Path(sys.prefix).resolve()
    script = """
import json, os, pathlib, subprocess, sys, time
sys.path.insert(0, '/opt/jarvis-source')
from tools.supervised_development_worker import BoundedDevelopmentWorker
workspace = pathlib.Path('/tmp/workspace')
workspace.mkdir()
(workspace / '.git').mkdir()
(workspace / '.git/HEAD').write_text('private repository administration')
(workspace / 'application.py').write_text('VALUE = 1\\n')
worker = BoundedDevelopmentWorker(workspace, worktree_root=pathlib.Path('/tmp/jobs'))
probe = (
    "import ctypes,os,pathlib,socket,subprocess; "
    "assert os.getuid() == 65534; "
    "assert 'JARVIS_FIXTURE_SECRET' not in os.environ; "
    "assert not pathlib.Path('/proc/self').exists(); "
    "assert not pathlib.Path('/run/docker.sock').exists(); "
    "assert not pathlib.Path('/usr/local/libexec').exists(); "
    "assert not pathlib.Path('/opt/jarvis-source').exists(); "
    "assert not pathlib.Path('/workspace/.git/HEAD').exists(); "
    "assert all(n=='lo' for _,n in socket.if_nameindex()); "
    "libc=ctypes.CDLL(None); header=(ctypes.c_uint32*2)(0x20080522,0); "
    "caps=(ctypes.c_uint32*6)(); "
    "assert libc.capget(ctypes.byref(header),ctypes.byref(caps))==0; "
    "assert caps[0]==caps[1]==caps[3]==caps[4]==0; "
    "assert libc.prctl(39,0,0,0,0)==1; "
    "assert subprocess.run(['/usr/bin/touch','/workspace/forbidden'],capture_output=True).returncode != 0; "
    "assert subprocess.run(['/usr/bin/touch','/proc/forbidden'],capture_output=True).returncode != 0; "
    "assert subprocess.run(['/usr/bin/git','commit','-am','forbidden'],capture_output=True).returncode != 0; "
    "assert subprocess.run(['/usr/bin/git','push'],capture_output=True).returncode != 0"
)
command = worker._sandbox_command(workspace, [sys.executable, '-I', '-c', probe])
index = command.index('/proc') - 1
assert command[index:index+4] == ['--tmpfs', '/proc', '--remount-ro', '/proc']
old_command = command[:index] + ['--proc', '/proc'] + command[index+4:]
old = subprocess.run(old_command, capture_output=True, text=True, timeout=10)
assert old.returncode != 0 and "Can't mount proc" in old.stderr, old.stderr
worker._sandbox_preflight(workspace, time.monotonic()+15)
code, output = worker._sandbox_run(workspace, [sys.executable, '-I', '-c', probe], time.monotonic()+15)
assert code == 0, output
assert not (workspace / 'forbidden').exists()
assert (workspace / 'application.py').read_text() == 'VALUE = 1\\n'
print(json.dumps({'original_proc_mount_denied':True,'verifier_passed':True,'uid':os.getuid()}))
"""
    command = [
        "docker",
        "run",
        "--rm",
        "--read-only",
        "--network",
        "none",
        "--user",
        "65534:65534",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges=true",
        "--security-opt",
        "seccomp=unconfined",
        "--security-opt",
        "apparmor=runc",
        "--pids-limit",
        "32",
        "--memory",
        "512m",
        "--cpus",
        "1",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,mode=1777",
        "--env",
        "JARVIS_FIXTURE_SECRET=disposable-not-a-credential",
        "--mount",
        "type=bind,src=/usr,dst=/usr,readonly",
        "--mount",
        f"type=bind,src={prefix},dst={prefix},readonly",
        "--mount",
        f"type=bind,src={source},dst=/opt/jarvis-source,readonly",
        "python:3.12-slim",
        sys.executable,
        "-I",
        "-c",
        script,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr[-5000:]
    assert json.loads(result.stdout) == {
        "original_proc_mount_denied": True,
        "verifier_passed": True,
        "uid": 65534,
    }
