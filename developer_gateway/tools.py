"""Capability-based developer tools; model-controlled command strings are never executed."""

import asyncio
import hashlib
import json
import os
import re
import signal
import uuid
from pathlib import Path
from typing import Any

from .policy import redact

READ = "read"
WORKSPACE = "workspace"
EXECUTION = "execution"
APPROVAL = "approval"
SPECS: dict[str, tuple[str, str, dict[str, Any]]] = {
    "repository": (
        READ,
        "Inspect repository status, diff, history or tracked file names.",
        {
            "view": {"type": "string", "enum": ["status", "diff", "history", "files"]},
        },
    ),
    "read_file": (
        READ,
        "Read at most 200 lines of a tracked source file.",
        {
            "path": {"type": "string"},
            "start": {"type": "integer", "minimum": 1},
        },
    ),
    "search": (
        READ,
        "Search literal text in bounded tracked source files.",
        {
            "text": {"type": "string", "minLength": 2, "maxLength": 100},
        },
    ),
    "create_worktree": (WORKSPACE, "Create or return this thread's isolated feature worktree.", {}),
    "patch_file": (
        WORKSPACE,
        "Replace/create/delete one bounded file with SHA256 precondition. "
        "expected_sha256 is empty only for a new file; null content deletes.",
        {
            "path": {"type": "string"},
            "expected_sha256": {"type": "string"},
            "content": {"type": ["string", "null"], "maxLength": 100000},
        },
    ),
    "verify": (
        EXECUTION,
        "Run a fixed verification profile, offline in a credential-free sandbox.",
        {
            "profile": {
                "type": "string",
                "enum": ["pytest", "ruff", "format", "compile", "script"],
            },
            "path": {"type": "string"},
        },
    ),
    "git_commit": (
        APPROVAL,
        "Commit task worktree changes on its isolated feature branch.",
        {
            "message": {"type": "string", "minLength": 1, "maxLength": 300},
        },
    ),
    "git_push": (APPROVAL, "Push only this task's feature branch; never force push.", {}),
    "pr_status": (READ, "Inspect this task's pull request and protected checks.", {}),
    "pr_create": (
        APPROVAL,
        "Create this task's PR into the canonical production branch.",
        {
            "title": {"type": "string", "maxLength": 120},
            "body": {"type": "string", "maxLength": 12000},
        },
    ),
    "pr_update": (
        APPROVAL,
        "Update only this task's PR description.",
        {
            "body": {"type": "string", "maxLength": 12000},
        },
    ),
    "pr_merge": (
        APPROVAL,
        "Squash this task's PR only when protected checks pass at exact head.",
        {},
    ),
}


def schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": name,
            "description": description,
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        }
        for name, (_, description, properties) in SPECS.items()
    ]


def validate(name: str, args: dict[str, Any]) -> str:
    if name not in SPECS or not isinstance(args, dict):
        raise ValueError("Unsupported tool")
    effect, _, properties = SPECS[name]
    if set(args) != set(properties):
        raise ValueError("Invalid tool arguments")
    for key, spec in properties.items():
        value = args[key]
        kinds = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
        kind = (
            "null"
            if value is None
            else (
                "string"
                if isinstance(value, str)
                else "integer"
                if type(value) is int
                else "invalid"
            )
        )
        if kind not in kinds:
            raise ValueError("Invalid argument type")
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError("Invalid argument choice")
        if isinstance(value, str) and not (
            spec.get("minLength", 0) <= len(value) <= spec.get("maxLength", 1000)
        ):
            raise ValueError("Invalid argument length")
        if kind == "integer" and value < spec.get("minimum", 0):
            raise ValueError("Invalid argument range")
    return effect


class ToolRunner:
    def __init__(self, root: Path, workspaces: Path, python: Path):
        self.root = root.resolve()
        self.workspaces = workspaces.resolve()
        self.python = python.absolute()
        self.isolation = os.getenv("JARVIS_DEVELOPER_SANDBOX", "bwrap")
        if self.isolation not in {"bwrap", "docker"}:
            raise ValueError("Unknown execution isolation")
        self.image = os.getenv("JARVIS_DEVELOPER_TOOL_IMAGE", "jarvis-developer-tools:local")
        self.owner = hashlib.sha256(str(self.workspaces).encode()).hexdigest()[:24]
        self.reconciled = False

    async def reconcile(self) -> None:
        if self.reconciled or self.isolation != "docker":
            return
        result = await self.command(
            [
                "/usr/bin/docker",
                "ps",
                "-aq",
                "--filter",
                "label=io.jarvis.developer.owner=" + self.owner,
            ],
            self.root,
        )
        if not result["ok"]:
            raise ValueError("Execution sandbox unavailable")
        for identifier in result["output"].splitlines():
            if not re.fullmatch(r"[0-9a-f]{12,64}", identifier):
                raise ValueError("Invalid sandbox identity")
            stopped = await self.command(["/usr/bin/docker", "rm", "-f", identifier], self.root)
            if not stopped["ok"]:
                raise ValueError("Previous execution needs reconciliation")
        self.reconciled = True

    async def command(self, argv: list[str], cwd: Path, timeout: float = 120) -> dict[str, Any]:
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(Path.home()),
            "LANG": "C.UTF-8",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_PAGER": "cat",
        }
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        output = bytearray()

        async def drain() -> None:
            assert process.stdout
            while chunk := await process.stdout.read(8192):
                output.extend(chunk[: max(0, 64000 - len(output))])
            await process.wait()

        try:
            await asyncio.wait_for(drain(), timeout)
        finally:
            # Also terminate descendants that outlive the approved command.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
            if argv[:2] == ["/usr/bin/docker", "run"] and "--name" in argv:
                name = argv[argv.index("--name") + 1]
                if re.fullmatch(r"jarvis-developer-[0-9a-f]{32}", name):
                    cleanup = await asyncio.create_subprocess_exec(
                        "/usr/bin/docker",
                        "rm",
                        "-f",
                        name,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await asyncio.wait_for(cleanup.wait(), 10)

        return {
            "ok": process.returncode == 0,
            "exit_code": process.returncode,
            "output": redact(output.decode(errors="replace")),
        }

    async def git(self, cwd: Path, *args: str) -> dict[str, Any]:
        return await self.command(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "diff.external=",
                "-c",
                "core.pager=cat",
                *args,
            ],
            cwd,
        )

    def workspace(self, thread: dict[str, Any], write: bool = False) -> Path:
        if not thread.get("worktree"):
            if write:
                raise ValueError("Create an isolated worktree before writing")
            return self.root
        expected = self.workspaces / thread["id"]
        if Path(thread["worktree"]) != expected or expected.is_symlink():
            raise ValueError("Workspace identity mismatch")
        if expected.resolve() == self.root or not expected.resolve().is_relative_to(
            self.workspaces
        ):
            raise ValueError("Production writes are forbidden")
        return expected

    def source(self, root: Path, name: str, *, directory: bool = False) -> Path:
        path = Path(name)
        if (
            not name
            or path.is_absolute()
            or any(
                part in {".", "..", ".git", ".ssh", ".gnupg", "data", "logs", "secrets"}
                or part.startswith(".env")
                for part in path.parts
            )
        ):
            raise ValueError("Unsafe source path")
        target = root / path
        if not target.resolve().is_relative_to(root):
            raise ValueError("Workspace escape rejected")
        for item in (target, *target.parents):
            if item == root:
                break
            if item.is_symlink():
                raise ValueError("Symlink source rejected")
        if (
            target.exists()
            and not (directory and target.is_dir())
            and (not target.is_file() or target.stat().st_nlink != 1)
        ):
            raise ValueError("Non-regular source rejected")
        return target

    async def files(self, root: Path) -> list[str]:
        result = await self.git(root, "ls-files", "-z")
        return [value for value in result["output"].split("\0") if value][:4000]

    async def assert_branch(self, thread: dict[str, Any]) -> Path:
        root = self.workspace(thread, True)
        branch = (await self.git(root, "branch", "--show-current"))["output"].strip()
        if branch != thread["branch"] or not branch.startswith("jarvis/developer-"):
            raise ValueError("Feature branch ownership changed")
        return root

    def sandbox(self, root: Path, argv: list[str], writable: bool) -> list[str]:
        # Same principle as worker candidate containers: no credentials, network,
        # host-home or Docker socket; workspace is the only persistent writable mount.
        if self.isolation == "docker":
            command = ["/usr/local/bin/python", *argv[1:]] if argv[0] == str(self.python) else argv
            return [
                "/usr/bin/docker",
                "run",
                "--rm",
                "--name",
                "jarvis-developer-" + uuid.uuid4().hex,
                "--label",
                "io.jarvis.developer.owner=" + self.owner,
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--memory",
                "2g",
                "--cpus",
                "2",
                "--pids-limit",
                "256",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=256m",
                "--env",
                "HOME=/tmp",
                "--mount",
                f"type=bind,src={root},dst=/workspace" + ("" if writable else ",readonly"),
                "--mount",
                "type=bind,src=/dev/null,dst=/workspace/.git,readonly",
                "--workdir",
                "/workspace",
                self.image,
                "/usr/bin/timeout",
                "--signal=KILL",
                "600",
                *command,
            ]
        venv = self.python.parent.parent
        return [
            "/usr/bin/prlimit",
            "--as=4294967296",
            "--cpu=600",
            "--fsize=134217728",
            "--",
            "/usr/bin/bwrap",
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--clearenv",
            "--ro-bind",
            "/usr",
            "/usr",
            "--symlink",
            "usr/bin",
            "/bin",
            "--symlink",
            "usr/lib",
            "/lib",
            "--symlink",
            "usr/lib64",
            "/lib64",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/home/sandbox",
            "--ro-bind",
            str(venv),
            str(venv),
            "--bind" if writable else "--ro-bind",
            str(root),
            "/workspace",
            "--ro-bind",
            "/dev/null",
            "/workspace/.git",
            "--setenv",
            "HOME",
            "/home/sandbox",
            "--setenv",
            "PATH",
            "/usr/bin:/bin",
            "--setenv",
            "PYTHONPATH",
            "/workspace/bridge:/workspace",
            "--setenv",
            "PYTHONDONTWRITEBYTECODE",
            "1",
            "--setenv",
            "LANG",
            "C.UTF-8",
            "--chdir",
            "/workspace",
            *argv,
        ]

    async def execute(self, thread: dict[str, Any], name: str, args: dict[str, Any]) -> Any:
        effect = validate(name, args)
        await self.reconcile()
        root = self.workspace(thread)
        if name == "create_worktree":
            if thread.get("worktree"):
                return {"ok": True, "worktree": str(root), "branch": thread["branch"]}
            self.workspaces.mkdir(mode=0o700, parents=True, exist_ok=True)
            branch = "jarvis/developer-" + thread["id"][4:]
            destination = self.workspaces / thread["id"]
            result = await self.git(
                self.root, "worktree", "add", "-b", branch, str(destination), "HEAD"
            )
            if not result["ok"]:
                raise ValueError("Worktree creation failed; reconciliation required")
            thread.update(worktree=str(destination), branch=branch)
            return {"ok": True, "worktree": str(destination), "branch": branch}
        if effect != READ:
            root = await self.assert_branch(thread)
        if name == "repository":
            commands = {
                "status": ["status", "--short"],
                "diff": ["diff", "--no-ext-diff", "--no-textconv", "HEAD"],
                "history": ["log", "-10", "--oneline"],
                "files": ["ls-files"],
            }
            result = await self.git(root, *commands[args["view"]])
            if args["view"] == "diff" and thread.get("worktree"):
                untracked = await self.git(root, "ls-files", "--others", "--exclude-standard", "-z")
                for relative in untracked["output"].split("\0")[:40]:
                    if not relative or len(result["output"]) >= 64000:
                        continue
                    try:
                        target = self.source(root, relative)
                        if target.stat().st_size > 100000:
                            continue
                        extra = await self.git(
                            root,
                            "diff",
                            "--no-index",
                            "--no-ext-diff",
                            "--no-textconv",
                            "--",
                            "/dev/null",
                            str(target),
                        )
                        # diff --no-index returns 1 for differences, not execution failure.
                        if extra["exit_code"] in {0, 1}:
                            result["output"] = (result["output"] + extra["output"])[:64000]
                    except (OSError, ValueError):
                        continue
            return result
        if name == "read_file":
            target = self.source(root, args["path"])
            if args["path"] not in await self.files(root):
                raise ValueError("Only tracked source may be read")
            if target.stat().st_size > 500000:
                raise ValueError("Source exceeds read limit")
            raw = target.read_bytes()
            content = raw.decode()
            return {
                "sha256": hashlib.sha256(raw).hexdigest(),
                "text": redact(
                    "\n".join(content.splitlines()[args["start"] - 1 : args["start"] + 199])[:16000]
                ),
            }
        if name == "search":
            matches = []
            for path in await self.files(root):
                try:
                    target = self.source(root, path)
                    if target.stat().st_size > 200000:
                        continue
                    for number, line in enumerate(target.read_text().splitlines(), 1):
                        if args["text"] in line:
                            matches.append(
                                {"path": path, "line": number, "text": redact(line[:300])}
                            )
                            if len(matches) >= 80:
                                return matches
                except (OSError, UnicodeError, ValueError):
                    continue
            return matches
        if name == "patch_file":
            target = self.source(root, args["path"])
            old = target.read_bytes() if target.exists() else None
            actual = hashlib.sha256(old).hexdigest() if old is not None else ""
            if actual != args["expected_sha256"]:
                raise ValueError("File changed: preimage does not match")
            if args["content"] is not None and args["content"].count("[REDACTED]") > (
                old.decode(errors="replace").count("[REDACTED]") if old is not None else 0
            ):
                raise ValueError("Refusing a replacement containing new redaction placeholders")
            if args["content"] is None:
                if old is None:
                    raise ValueError("File does not exist")
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(args["content"])
            return {
                "ok": True,
                "path": args["path"],
                "sha256": hashlib.sha256((args["content"] or "").encode()).hexdigest(),
            }
        if name == "verify":
            await self.reconcile()
            path = args["path"]
            if path:
                self.source(root, path, directory=True)
            profile = args["profile"]
            python = str(self.python)
            commands = {
                "pytest": [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", path],
                "ruff": [python, "-m", "ruff", "check", "--no-cache", path],
                "format": [python, "-m", "ruff", "format", "--no-cache", path],
                "compile": [python, "-m", "compileall", "-q", path],
                "script": ["/bin/bash", path],
            }
            if not path or path.startswith("-"):
                raise ValueError("A bounded verification path is required")
            if profile == "script" and not path.startswith("tools/"):
                raise ValueError("Only repository verification scripts are permitted")
            return await self.command(
                self.sandbox(root, commands[profile], True),
                root,
                600,
            )
        if name == "git_commit":
            result = await self.git(root, "add", "--all")
            if result["ok"]:
                result = await self.git(
                    root,
                    "-c",
                    "user.name=Jarvis Developer",
                    "-c",
                    "user.email=jarvis@localhost",
                    "-c",
                    "commit.gpgsign=false",
                    "commit",
                    "-m",
                    args["message"],
                )
            return result
        if name == "git_push":
            return await self.git(
                root,
                "-c",
                "credential.helper=!gh auth git-credential",
                "push",
                "origin",
                f"HEAD:refs/heads/{thread['branch']}",
            )
        if name.startswith("pr_"):
            if not thread.get("branch"):
                if name == "pr_status":
                    return {"ok": False, "error": "This task has no branch or pull request yet"}
                raise ValueError("No task branch")
            branch = thread["branch"]
            view = await self.command(
                [
                    "gh",
                    "pr",
                    "view",
                    branch,
                    "--json",
                    "number,headRefOid,headRefName,baseRefName,statusCheckRollup,state",
                ],
                root,
            )
            if name == "pr_status":
                return view
            if name == "pr_create":
                if view["ok"]:
                    return view
                return await self.command(
                    [
                        "gh",
                        "pr",
                        "create",
                        "--head",
                        branch,
                        "--base",
                        "jarvis/unified-production",
                        "--title",
                        args["title"],
                        "--body",
                        args["body"],
                    ],
                    root,
                )
            if not view["ok"]:
                raise ValueError("Task PR unavailable")
            pr = json.loads(view["output"])
            if pr["headRefName"] != branch or pr["baseRefName"] != "jarvis/unified-production":
                raise ValueError("PR ownership mismatch")
            if name == "pr_update":
                return await self.command(
                    [
                        "gh",
                        "api",
                        "--method",
                        "PATCH",
                        f"repos/{{owner}}/{{repo}}/pulls/{pr['number']}",
                        "-f",
                        "body=" + args["body"],
                        "--jq",
                        ".number",
                    ],
                    root,
                )
            checks = {c.get("name"): c.get("conclusion") for c in pr["statusCheckRollup"]}
            required = {"core-tests", "quality-security", "android-unit-tests", "Analyze Python"}
            if not required.issubset(checks) or any(checks[k] != "SUCCESS" for k in required):
                raise ValueError("Protected checks have not all passed")
            head = (await self.git(root, "rev-parse", "HEAD"))["output"].strip()
            if head != pr["headRefOid"]:
                raise ValueError("PR head changed")
            return await self.command(
                ["gh", "pr", "merge", str(pr["number"]), "--squash", "--match-head-commit", head],
                root,
            )
        raise ValueError("Unsupported tool")
