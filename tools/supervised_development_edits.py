"""Pure structured-edit machinery shared by legacy and supervised workers.

No provider, database, credential, integration, or deployment imports belong here.
Existing exact-once and non-overlap checks are preserved; additions are explicit,
scope-bound and rolled back along with replacements when validation fails.
"""

from __future__ import annotations

import hashlib
from fnmatch import fnmatch
import re
import subprocess
from pathlib import Path
from typing import Any


class StructuredEditError(RuntimeError):
    pass


def _git(workspace: Path, *args: str) -> str:
    if args and args[0] == "diff":
        args = ("diff", "--no-ext-diff", "--no-textconv", *args[1:])
    return subprocess.run(
        ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args],
        cwd=workspace,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1"},
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    ).stdout


def _matches(path: str, patterns: list[str]) -> bool:
    # Retain the existing policy's glob semantics. Supervised callers supply
    # only canonical, exact signed paths; the legacy engine retains its policy.
    return any(fnmatch(path, pattern) for pattern in patterns)


def apply_incremental_edits(
    workspace: Path,
    payload: dict[str, Any],
    policy: dict[str, Any],
    config: Any,
    *,
    allow_additions: bool = False,
) -> tuple[str, list[str], str]:
    edits = payload.get("edits")
    if not isinstance(edits, list) or not edits:
        raise StructuredEditError("Development step returned no structured edits.")
    if len(edits) > config.max_patch_lines:
        raise StructuredEditError("Development step contains too many structured edits.")
    grouped: dict[str, list[tuple[str, str]]] = {}
    for index, edit in enumerate(edits, start=1):
        if not isinstance(edit, dict):
            raise StructuredEditError(f"Development edit {index} is not an object.")
        path, old, new = edit.get("path"), edit.get("old_text"), edit.get("new_text")
        if (
            not isinstance(path, str)
            or not re.fullmatch(r"[A-Za-z0-9_./-]{1,240}", path)
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            raise StructuredEditError("Unsafe structured edit path.")
        if not _matches(path, policy.get("allowed_edit_paths", [])) or _matches(
            path, policy.get("forbidden_paths", [])
        ):
            raise StructuredEditError(
                f"Structured edit touches path outside the allow-list: {path}"
            )
        if not isinstance(old, str) or (not old and not allow_additions):
            raise StructuredEditError(f"Development edit {index} has invalid old_text.")
        if not isinstance(new, str) or old == new:
            raise StructuredEditError(f"Development edit {index} has invalid new_text.")
        grouped.setdefault(path, []).append((old, new))
    if len(grouped) > config.max_changed_files:
        raise StructuredEditError("Development changed-file limit exceeded.")
    originals: dict[Path, str | None] = {}
    updated: dict[Path, str] = {}
    created_dirs: list[Path] = []
    added: list[str] = []
    root = workspace.resolve()
    try:
        for path, replacements in grouped.items():
            target = workspace / path
            if (
                target.is_symlink()
                or not target.resolve().is_relative_to(root)
                or any(
                    parent.is_symlink()
                    for parent in target.parents
                    if parent != root and parent.is_relative_to(root)
                )
            ):
                raise StructuredEditError(f"Development target is not a safe regular file: {path}")
            if not target.exists():
                if not allow_additions or len(replacements) != 1 or replacements[0][0] != "":
                    raise StructuredEditError(
                        f"Development target is not a safe regular file: {path}"
                    )
                originals[target] = None
                updated[target] = replacements[0][1]
                added.append(path)
                continue
            if not target.is_file():
                raise StructuredEditError(f"Development target is not a safe regular file: {path}")
            original = target.read_text(encoding="utf-8")
            originals[target] = original
            spans: list[tuple[int, int, str]] = []
            for old, new in replacements:
                if not old or original.count(old) != 1:
                    raise StructuredEditError(
                        f"Development old_text for {path} occurs {original.count(old)} times; exactly one is required."
                    )
                start, end = original.index(old), original.index(old) + len(old)
                if any(
                    start < other_end and end > other_start for other_start, other_end, _ in spans
                ):
                    raise StructuredEditError(f"Development edits overlap in {path}.")
                spans.append((start, end, new))
            for start, end, new in sorted(spans, reverse=True):
                original = original[:start] + new + original[end:]
            updated[target] = original
        for target, content in updated.items():
            missing = []
            parent = target.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir(mode=0o700)
                created_dirs.append(directory)
            target.write_text(content, encoding="utf-8")
        if added:
            _git(workspace, "add", "--intent-to-add", "--", *added)
        patch = _git(workspace, "diff", "--no-ext-diff", "--binary", "HEAD", "--")
        paths = sorted(set(_git(workspace, "diff", "--name-only", "HEAD", "--").splitlines()))
        if not patch.strip() or not paths:
            raise StructuredEditError("Development edits produced no Git diff.")
        if len(paths) > config.max_changed_files or any(
            not _matches(path, policy.get("allowed_edit_paths", []))
            or _matches(path, policy.get("forbidden_paths", []))
            for path in paths
        ):
            raise StructuredEditError("Development patch is outside the allow-list.")
        count = sum(
            1
            for line in patch.splitlines()
            if line[:1] in {"+", "-"} and not line.startswith(("+++", "---"))
        )
        if count > config.max_patch_lines:
            raise StructuredEditError("Development patch line limit exceeded.")
        additions = "\n".join(
            line[1:]
            for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        if any(
            re.search(pattern, additions, re.I)
            for pattern in policy.get("forbidden_added_patterns", [])
        ):
            raise StructuredEditError("Development patch adds a forbidden construct.")
        return patch, paths, hashlib.sha256(patch.encode()).hexdigest()
    except BaseException:
        if added:
            _git(workspace, "reset", "--quiet", "HEAD", "--", *added)
        for target, previous in originals.items():
            if previous is None:
                target.unlink(missing_ok=True)
            else:
                target.write_text(previous, encoding="utf-8")
        for directory in reversed(created_dirs):
            directory.rmdir()
        raise
