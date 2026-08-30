import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


class CodeAwarenessError(RuntimeError):
    """Raised when a Code Awareness request is outside its read-only boundary."""


class CodeAwarenessEngine:
    """Read-only inspection of explicitly mounted source repositories."""

    TOOL_NAMES = frozenset(
        {
            "code_roots",
            "code_list",
            "code_read",
            "code_search",
            "git_status",
            "git_diff",
            "git_log",
        }
    )

    ROOT_NAMES = ("jarvis", "voice_pe")

    DENIED_DIR_NAMES = {
        ".git",
        ".ssh",
        ".gnupg",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        "node_modules",
        "speaker-data",
        "backup",
        "backups",
        "secrets",
        "credentials",
    }

    DENIED_FILE_PATTERN = re.compile(
        r"^(?:"
        r"\.env(?:\..*)?|"
        r"id_rsa(?:\.pub)?|"
        r"id_ed25519(?:\.pub)?|"
        r".*\.(?:pem|key|p12|pfx)"
        r")$",
        re.I,
    )

    TEXT_SUFFIXES = {
        ".py",
        ".pyi",
        ".yaml",
        ".yml",
        ".json",
        ".toml",
        ".md",
        ".txt",
        ".sh",
        ".bash",
        ".zsh",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".java",
        ".kt",
        ".kts",
        ".gradle",
        ".xml",
        ".html",
        ".css",
        ".scss",
        ".c",
        ".cc",
        ".cpp",
        ".h",
        ".hpp",
        ".ini",
        ".cfg",
        ".conf",
        ".properties",
        ".sql",
    }

    SPECIAL_TEXT_NAMES = {
        "Dockerfile",
        "Makefile",
        "Procfile",
        "requirements.txt",
        ".gitignore",
        ".dockerignore",
    }

    MAX_READ_LINES = 400
    MAX_LINE_CHARS = 600
    MAX_SEARCH_FILES = 3000
    MAX_SEARCH_RESULTS = 50
    MAX_GIT_OUTPUT_CHARS = 60000

    def __init__(
        self,
        *,
        enabled: bool,
        roots: dict[str, Path],
    ) -> None:
        self.enabled = bool(enabled)
        self.roots = {str(name): Path(path) for name, path in roots.items()}

    @classmethod
    def from_environment(cls) -> "CodeAwarenessEngine":
        enabled = os.getenv(
            "JARVIS_CODE_AWARENESS_ENABLED",
            "false",
        ).strip().lower() in {"1", "true", "yes", "on"}

        return cls(
            enabled=enabled,
            roots={
                "jarvis": Path("/host-code/jarvis"),
                "voice_pe": Path("/host-code/jarvis-voice-direct-alpha1"),
            },
        )

    def status(self) -> dict[str, Any]:
        return {
            "success": True,
            "enabled": self.enabled,
            "mode": "read_only",
            "roots": {
                name: {
                    "path": str(path),
                    "available": path.exists(),
                }
                for name, path in self.roots.items()
            },
        }

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise CodeAwarenessError("Code Awareness is disabled.")

    def _root(self, root_name: str) -> Path:
        self._require_enabled()

        name = str(root_name or "").strip()

        if name not in self.roots:
            raise CodeAwarenessError(f"Unknown code root: {name}")

        root = self.roots[name]

        if not root.exists():
            raise CodeAwarenessError(f"Code root is not mounted: {name}")

        return root.resolve()

    @classmethod
    def _denied_relative(
        cls,
        relative: Path,
    ) -> bool:
        for part in relative.parts:
            if part.casefold() in {item.casefold() for item in cls.DENIED_DIR_NAMES}:
                return True

        if relative.parts:
            filename = relative.parts[-1]
            if cls.DENIED_FILE_PATTERN.match(filename):
                return True

        return False

    def _resolve(
        self,
        root_name: str,
        relative_path: str,
    ) -> tuple[Path, Path, Path]:
        root = self._root(root_name)

        raw = str(relative_path or "").strip()
        relative = Path(raw or ".")

        if relative.is_absolute():
            raise CodeAwarenessError("Absolute paths are not permitted.")

        candidate = (root / relative).resolve()

        try:
            resolved_relative = candidate.relative_to(root)
        except ValueError as exc:
            raise CodeAwarenessError("Path escapes the permitted code root.") from exc

        if self._denied_relative(resolved_relative):
            raise CodeAwarenessError("That path is restricted.")

        return root, candidate, resolved_relative

    @classmethod
    def _is_text_file(
        cls,
        path: Path,
    ) -> bool:
        if path.name in cls.SPECIAL_TEXT_NAMES:
            return True

        return path.suffix.casefold() in cls.TEXT_SUFFIXES

    @classmethod
    def _safe_display_line(
        cls,
        value: str,
    ) -> str:
        value = value.rstrip("\r\n")

        if len(value) > cls.MAX_LINE_CHARS:
            return value[: cls.MAX_LINE_CHARS] + " …"

        return value

    def roots_result(self) -> dict[str, Any]:
        self._require_enabled()

        return {
            "success": True,
            "mode": "read_only",
            "roots": [
                {
                    "name": name,
                    "available": path.exists(),
                }
                for name, path in self.roots.items()
            ],
        }

    def list_path(
        self,
        root_name: str,
        relative_path: str,
        recursive: bool,
    ) -> dict[str, Any]:
        root, candidate, relative = self._resolve(
            root_name,
            relative_path,
        )

        if not candidate.exists():
            raise CodeAwarenessError("Requested path does not exist.")

        if not candidate.is_dir():
            raise CodeAwarenessError("code_list requires a directory.")

        items: list[dict[str, Any]] = []

        if not recursive:
            for entry in sorted(
                candidate.iterdir(),
                key=lambda item: item.name.casefold(),
            ):
                if entry.is_symlink():
                    continue

                rel = entry.relative_to(root)

                if self._denied_relative(rel):
                    continue

                if entry.is_dir():
                    items.append(
                        {
                            "path": rel.as_posix(),
                            "type": "directory",
                        }
                    )
                elif entry.is_file() and self._is_text_file(entry):
                    items.append(
                        {
                            "path": rel.as_posix(),
                            "type": "file",
                            "size_bytes": entry.stat().st_size,
                        }
                    )

                if len(items) >= 200:
                    break

        else:
            start_depth = len(relative.parts)

            for current, directories, filenames in os.walk(
                candidate,
                followlinks=False,
            ):
                current_path = Path(current)
                current_relative = current_path.relative_to(root)

                depth = len(current_relative.parts) - start_depth

                directories[:] = [
                    name
                    for name in directories
                    if not self._denied_relative(current_relative / name)
                ]

                if depth >= 2:
                    directories[:] = []

                for name in sorted(filenames):
                    path = current_path / name

                    if path.is_symlink():
                        continue

                    rel = path.relative_to(root)

                    if self._denied_relative(rel):
                        continue

                    if not self._is_text_file(path):
                        continue

                    items.append(
                        {
                            "path": rel.as_posix(),
                            "type": "file",
                            "size_bytes": path.stat().st_size,
                        }
                    )

                    if len(items) >= 200:
                        break

                if len(items) >= 200:
                    break

        return {
            "success": True,
            "root": root_name,
            "path": relative.as_posix(),
            "recursive": bool(recursive),
            "count": len(items),
            "items": items,
            "truncated": len(items) >= 200,
        }

    def read_file(
        self,
        root_name: str,
        relative_path: str,
        start_line: int,
        end_line: int,
    ) -> dict[str, Any]:
        _, candidate, relative = self._resolve(
            root_name,
            relative_path,
        )

        if not candidate.exists():
            raise CodeAwarenessError("Requested file does not exist.")

        if not candidate.is_file():
            raise CodeAwarenessError("code_read requires a file.")

        if not self._is_text_file(candidate):
            raise CodeAwarenessError("That file type is not available to Code Awareness.")

        start = max(1, int(start_line))
        end = max(start, int(end_line))
        end = min(
            end,
            start + self.MAX_READ_LINES - 1,
        )

        selected: list[str] = []
        total_lines = 0

        with candidate.open(
            "r",
            encoding="utf-8",
            errors="replace",
        ) as handle:
            for number, line in enumerate(
                handle,
                start=1,
            ):
                total_lines = number

                if number < start:
                    continue

                if number > end:
                    break

                selected.append(f"{number}: " + self._safe_display_line(line))

        return {
            "success": True,
            "root": root_name,
            "path": relative.as_posix(),
            "start_line": start,
            "end_line": (start + len(selected) - 1 if selected else start),
            "total_lines_seen": total_lines,
            "content": "\n".join(selected),
            "read_only": True,
        }

    def search(
        self,
        root_name: str,
        relative_path: str,
        query: str,
        max_results: int,
    ) -> dict[str, Any]:
        root, candidate, _ = self._resolve(
            root_name,
            relative_path,
        )

        needle = str(query or "").strip()

        if not needle:
            raise CodeAwarenessError("Search query is empty.")

        limit = max(
            1,
            min(
                int(max_results),
                self.MAX_SEARCH_RESULTS,
            ),
        )

        if not candidate.exists():
            raise CodeAwarenessError("Requested search path does not exist.")

        files: list[Path] = []

        if candidate.is_file():
            if not self._is_text_file(candidate):
                raise CodeAwarenessError("That file type is not searchable.")
            files = [candidate]

        elif candidate.is_dir():
            for current, directories, filenames in os.walk(
                candidate,
                followlinks=False,
            ):
                current_path = Path(current)
                current_relative = current_path.relative_to(root)

                directories[:] = [
                    name
                    for name in directories
                    if not self._denied_relative(current_relative / name)
                ]

                for name in filenames:
                    path = current_path / name

                    if path.is_symlink():
                        continue

                    relative = path.relative_to(root)

                    if self._denied_relative(relative):
                        continue

                    if not self._is_text_file(path):
                        continue

                    files.append(path)

                    if len(files) >= self.MAX_SEARCH_FILES:
                        break

                if len(files) >= self.MAX_SEARCH_FILES:
                    break

        else:
            raise CodeAwarenessError("Unsupported search path.")

        folded_needle = needle.casefold()
        matches: list[dict[str, Any]] = []

        for path in files:
            try:
                with path.open(
                    "r",
                    encoding="utf-8",
                    errors="replace",
                ) as handle:
                    for number, line in enumerate(
                        handle,
                        start=1,
                    ):
                        if folded_needle not in line.casefold():
                            continue

                        matches.append(
                            {
                                "path": (path.relative_to(root).as_posix()),
                                "line": number,
                                "text": self._safe_display_line(line),
                            }
                        )

                        if len(matches) >= limit:
                            break
            except OSError:
                continue

            if len(matches) >= limit:
                break

        return {
            "success": True,
            "root": root_name,
            "query": needle,
            "scanned_files": len(files),
            "count": len(matches),
            "matches": matches,
            "truncated": len(matches) >= limit,
            "read_only": True,
        }

    def _safe_git_path(
        self,
        root_name: str,
        relative_path: str,
    ) -> tuple[Path, str]:
        root = self._root(root_name)
        raw = str(relative_path or "").strip()

        if not raw:
            return root, ""

        relative = Path(raw)

        if relative.is_absolute() or ".." in relative.parts:
            raise CodeAwarenessError("Invalid Git path.")

        if self._denied_relative(relative):
            raise CodeAwarenessError("That Git path is restricted.")

        return root, relative.as_posix()

    def _run_git(
        self,
        root_name: str,
        args: list[str],
    ) -> dict[str, Any]:
        root = self._root(root_name)

        git = shutil.which("git")

        if not git:
            raise CodeAwarenessError("Git is not installed in Jarvis Core.")

        command = [
            git,
            "-c",
            f"safe.directory={root}",
            "-C",
            str(root),
            *args,
        ]

        env = {
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "HOME": "/tmp/jarvis-code-awareness",
        }

        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            env=env,
            check=False,
        )

        output = completed.stdout or ""

        if len(output) > self.MAX_GIT_OUTPUT_CHARS:
            output = output[: self.MAX_GIT_OUTPUT_CHARS] + "\n… output truncated …"

        return {
            "success": completed.returncode == 0,
            "root": root_name,
            "returncode": completed.returncode,
            "output": output.strip(),
            "read_only": True,
        }

    def git_status(
        self,
        root_name: str,
    ) -> dict[str, Any]:
        return self._run_git(
            root_name,
            [
                "status",
                "--short",
                "--branch",
                "--untracked-files=normal",
            ],
        )

    def git_diff(
        self,
        root_name: str,
        relative_path: str,
        staged: bool,
    ) -> dict[str, Any]:
        _, safe_path = self._safe_git_path(
            root_name,
            relative_path,
        )

        args = [
            "diff",
            "--no-ext-diff",
            "--unified=3",
        ]

        if staged:
            args.append("--cached")

        if safe_path:
            args.extend(
                [
                    "--",
                    safe_path,
                ]
            )

        return self._run_git(
            root_name,
            args,
        )

    def git_log(
        self,
        root_name: str,
        limit: int,
    ) -> dict[str, Any]:
        count = max(
            1,
            min(
                int(limit),
                30,
            ),
        )

        return self._run_git(
            root_name,
            [
                "log",
                f"-{count}",
                "--oneline",
                "--decorate",
            ],
        )

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            if name == "code_roots":
                return await asyncio.to_thread(self.roots_result)

            if name == "code_list":
                return await asyncio.to_thread(
                    self.list_path,
                    str(arguments.get("root") or ""),
                    str(arguments.get("path") or ""),
                    bool(arguments.get("recursive")),
                )

            if name == "code_read":
                return await asyncio.to_thread(
                    self.read_file,
                    str(arguments.get("root") or ""),
                    str(arguments.get("path") or ""),
                    int(arguments.get("start_line") or 1),
                    int(arguments.get("end_line") or 200),
                )

            if name == "code_search":
                return await asyncio.to_thread(
                    self.search,
                    str(arguments.get("root") or ""),
                    str(arguments.get("path") or ""),
                    str(arguments.get("query") or ""),
                    int(arguments.get("max_results") or 30),
                )

            if name == "git_status":
                return await asyncio.to_thread(
                    self.git_status,
                    str(arguments.get("root") or ""),
                )

            if name == "git_diff":
                return await asyncio.to_thread(
                    self.git_diff,
                    str(arguments.get("root") or ""),
                    str(arguments.get("path") or ""),
                    bool(arguments.get("staged")),
                )

            if name == "git_log":
                return await asyncio.to_thread(
                    self.git_log,
                    str(arguments.get("root") or ""),
                    int(arguments.get("limit") or 10),
                )

            raise CodeAwarenessError(f"Unsupported Code Awareness tool: {name}")

        except (
            CodeAwarenessError,
            OSError,
            ValueError,
            subprocess.SubprocessError,
        ) as exc:
            return {
                "success": False,
                "error": {
                    "code": "code_awareness_error",
                    "message": str(exc),
                },
                "read_only": True,
            }

    def openai_tools(self) -> list[dict[str, Any]]:
        roots = list(self.ROOT_NAMES)

        return [
            {
                "type": "function",
                "name": "code_roots",
                "description": (
                    "List the live source-code repositories Jarvis is permitted to inspect."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "strict": True,
            },
            {
                "type": "function",
                "name": "code_list",
                "description": (
                    "List source files and directories inside one "
                    "permitted live code repository. Read-only."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "root": {
                            "type": "string",
                            "enum": roots,
                        },
                        "path": {
                            "type": "string",
                            "description": (
                                "Repository-relative directory path. "
                                "Use an empty string for repository root."
                            ),
                        },
                        "recursive": {
                            "type": "boolean",
                        },
                    },
                    "required": [
                        "root",
                        "path",
                        "recursive",
                    ],
                    "additionalProperties": False,
                },
                "strict": True,
            },
            {
                "type": "function",
                "name": "code_read",
                "description": (
                    "Read a bounded line range from a live source "
                    "file. Secret and credential paths are blocked."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "root": {
                            "type": "string",
                            "enum": roots,
                        },
                        "path": {
                            "type": "string",
                        },
                        "start_line": {
                            "type": "integer",
                            "minimum": 1,
                        },
                        "end_line": {
                            "type": "integer",
                            "minimum": 1,
                        },
                    },
                    "required": [
                        "root",
                        "path",
                        "start_line",
                        "end_line",
                    ],
                    "additionalProperties": False,
                },
                "strict": True,
            },
            {
                "type": "function",
                "name": "code_search",
                "description": (
                    "Search literal text across live source files "
                    "inside a permitted repository. Read-only."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "root": {
                            "type": "string",
                            "enum": roots,
                        },
                        "path": {
                            "type": "string",
                            "description": (
                                "Repository-relative file or directory. "
                                "Use empty string for the full repository."
                            ),
                        },
                        "query": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 200,
                        },
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 50,
                        },
                    },
                    "required": [
                        "root",
                        "path",
                        "query",
                        "max_results",
                    ],
                    "additionalProperties": False,
                },
                "strict": True,
            },
            {
                "type": "function",
                "name": "git_status",
                "description": (
                    "Inspect the live Git working-tree status for a "
                    "permitted repository without changing it."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "root": {
                            "type": "string",
                            "enum": roots,
                        },
                    },
                    "required": ["root"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
            {
                "type": "function",
                "name": "git_diff",
                "description": (
                    "Read the live Git diff for a permitted repository "
                    "or source path. Does not modify the repository."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "root": {
                            "type": "string",
                            "enum": roots,
                        },
                        "path": {
                            "type": "string",
                        },
                        "staged": {
                            "type": "boolean",
                        },
                    },
                    "required": [
                        "root",
                        "path",
                        "staged",
                    ],
                    "additionalProperties": False,
                },
                "strict": True,
            },
            {
                "type": "function",
                "name": "git_log",
                "description": ("Read recent Git commit history from a permitted live repository."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "root": {
                            "type": "string",
                            "enum": roots,
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 30,
                        },
                    },
                    "required": [
                        "root",
                        "limit",
                    ],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        ]
