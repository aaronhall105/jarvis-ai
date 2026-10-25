"""Root-free release packaging check; never invokes the live installer."""

import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def test_installer_includes_transitive_gateway_modules(tmp_path):
    source = Path(__file__).resolve().parents[1]
    script = (source / "install.sh").read_text()
    match = re.search(r"for source_file in ([^;]+); do", script)
    assert match
    files = set(match[1].split())
    assert {"app.py", "autonomy.py", "autonomy_api.py", "autonomy_host.py", "observer.py"} <= files
    target = tmp_path / "developer_gateway"
    target.mkdir()
    for name in files:
        assert re.fullmatch(r"[a-z_]+\.py", name)
        shutil.copyfile(source / name, target / name)
        for node in ast.walk(ast.parse((source / name).read_text())):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                assert node.module.split(".")[0] + ".py" in files
    result = subprocess.run(
        [sys.executable, "-c", "import developer_gateway.autonomy_api, developer_gateway.observer"],
        cwd=tmp_path,
        env={"PATH": os.defpath, "PYTHONPATH": str(tmp_path)},
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, "Packaged observer imports failed"
