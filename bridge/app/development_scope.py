"""Canonical non-privileged edit policy shared with the protected worker.

The installer copies this dependency-free module into the protected tools
package. Exact owner scope can narrow this policy, never enlarge it.
"""

import re
from pathlib import PurePosixPath

PROTECTED_PREFIXES = (
    ".",
    "data/",
    "logs/",
    "systemd/",
    "developer_gateway/",
    "updates/",
    "tools/autonomy_",
    "tools/supervised_development_",
    "tools/self_improvement",
    "tools/self_development",
    "tools/deploy_",
    "tools/install_",
    "tools/enroll_",
    "tools/artifact_signing",
    "tools/publish_",
    "tools/generate_ota",
    "tools/verify_",
    "tools/check_",
    "tools/prepare_",
    "bridge/app/main.py",
    "bridge/app/config.py",
    "bridge/app/admin_engine.py",
    "bridge/app/self_improvement.py",
    "bridge/app/development_scope.py",
    "docs/JARVIS_PRODUCT_BASELINE.json",
    "docs/releases/",
)
PROTECTED_WORDS = re.compile(
    r"(?:^|[/_.-])(?:auth(?:entication|orization)?|security|secrets?|credentials?|passwords?|"
    r"tokens?|signing|keystore|guardian|broker|policy|policies|deploy(?:ment)?|sudoers|"
    r"permissions?|approvals?|patch_validation)(?:$|[/_.-])",
    re.I,
)


def editable_path(value: object) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_./-]{1,240}", value):
        return False
    if value.startswith(PROTECTED_PREFIXES) or PROTECTED_WORDS.search(value):
        return False
    parts = value.split("/")
    if any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
        return False
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value:
        return False
    if path.name.startswith(("docker-compose.", "compose.")) or path.name in {
        "conftest.py",
        "sitecustomize.py",
        "usercustomize.py",
        "pytest.ini",
        "setup.cfg",
        "ruff.toml",
    }:
        return False
    if path.name in {
        "Dockerfile",
        "docker-compose.yml",
        "compose.yml",
        "requirements.txt",
        "pyproject.toml",
    }:
        return False
    return path.suffix in {".py", ".pyi", ".toml", ".yaml", ".yml", ".json", ".md"}
