# Jarvis Developer mode

Developer mode is a second, explicitly selected backend in the existing phone chat UI. Normal
Jarvis messages continue to use Jarvis Core. Developer messages use an authenticated gateway on
the Ubuntu Jarvis PC. The default backend is a server-owned Developer Agent using the normal
OpenAI Responses API, billed to the API account, not the ChatGPT/Codex allowance.

## Architecture

`DeveloperBackends` routes the stable Jarvis thread/turn protocol to
`OpenAIResponsesDeveloperBackend` or `CodexAppServerDeveloperBackend`.
`JARVIS_DEVELOPER_BACKEND=api|codex|auto` controls **new** threads. `api` never invokes a
Codex model turn. `codex` selects the retained App Server. `auto` selects configured API access
unless a provider failure has been observed, otherwise Codex, only at thread creation.
A successful explicit API retry clears that failure state. Existing threads never switch providers, including
after provider errors. An `api_` identity belongs exclusively to the API journal.

Responses uses strict function schemas, streamed text, `previous_response_id` continuation,
and server-side compaction at 32,000 tokens. The configurable model is
`JARVIS_DEVELOPER_MODEL` (default `gpt-5.4`); no tool implementation depends on a model name.
The agent resends its stable policy and exact goal/worktree/branch/error state on each request.
Compaction never deletes the local approval, tool-outcome, or history journals.
Source reads, searches and command outputs are bounded; the whole repository is never sent.

Only the allow-listed `jarvis` and compatibility alias `jarvis-wear` workspaces are selectable.
They identify the canonical repository for reads; the API tool executor cannot write there.
The legacy `codex.event` envelope remains for installed Android compatibility, carrying Jarvis
assistant/activity/approval/turn concepts, not raw OpenAI events. Reasoning events are omitted.
Live and history Android rendering share `DeveloperActivityPolicy.messageText`.

The gateway binds to `127.0.0.1:8765`. Remote access is intended to use Tailscale Serve HTTPS/WSS
at `/developer`; the raw port must not be exposed publicly. The Android credential is separate
from Jarvis Core credentials and is encrypted with Android Keystore. The Ubuntu token is loaded
from a systemd credential backed by a mode-0600 file.

The phone resumes a persisted backend-pinned thread after reconnecting. Jobs continue on the
PC if the Android UI disconnects, and the session can be resumed after reopening the app. Network
callbacks reorder local and secure-remote endpoints and reconnect with bounded exponential
backoff. The phone microphone remains off unless the user explicitly starts voice input.

## Security model

- Authentication fails closed when the gateway token is absent.
- TLS is terminated inside the private Tailscale network; no public command socket is created.
- Workspaces are selected by fixed identifier, then canonicalised and checked against the
  allow-list.
- API tools have explicit `read`, `workspace`, `execution`, and `approval` effect classes.
  Unknown tools and nonconforming arguments fail closed. There is no model-facing shell.
- Every task worktree has a server-generated branch and directory. Canonical branch checks,
  path canonicalization, symlink/hardlink rejection, file-size limits and SHA256 preconditions
  protect edits. Worktree creation does not delete or reset existing worktrees.
- Tests, formatters and repository scripts run in a dependency-only Docker image with no network, credentials,
  host home, Git metadata, or Docker socket. Only the isolated task tree is persistently writable.
  The container root is read-only, capabilities dropped, privileges disabled, and CPU/memory/PID/time
  budgets enforced. Named executions are removed on cancellation; startup reconciles only this
  agent's labelled orphan containers before new tools run. Local development may explicitly use
  Bubblewrap/prlimit instead. No unsandboxed fallback exists.
- Git commit/push and task PR create/update/merge require an exact-operation approval.
  The legacy Android `acceptForSession` choice grants **only that recorded API call**.
  Approval payloads display the exact operation and expire after 15 minutes.
  Merge is squash-only, requires exact head identity and all four protected CI checks.
- Canonical production deployment, credentials/configuration changes, destructive Git and broad
  deletion are **not exposed** by the API tool registry. They require an independently approved
  operator workflow; the agent cannot escalate by constructing a command string.
- Optional Codex sessions use read-only canonical workspace access with on-request escalation.
  Legacy thread IDs/history remain in App Server storage; API history is separate.
- Events are filtered to threads owned or resumed by that authenticated WebSocket connection.
- Requests are rate limited. Audit logs use fixed operation-event names and never include
  user-supplied workspace values, prompts, tokens, file contents, or command output.
- Destructive actions remain subject to Codex approvals and the existing repository rules.

## Durable jobs and exact-call execution

Private SQLite state lives in `~/.local/share/jarvis-developer/agent/tasks.sqlite3`.
It contains backend/thread/turn identity, original goal, authorized worktree/branch,
provider response reference, pending provider output, usage, history, approvals and tool results.
An exclusive process lock prevents two gateway workers owning the same journal.
Use one Uvicorn worker; sidecars must use a separate `JARVIS_DEVELOPER_STATE_DIR`.

Before a tool executes, its `(thread, turn, call_id)` and argument digest are committed.
Results are committed **before** returning them to the model. A repeated completed call
returns the saved result. Reusing a call ID with different arguments is rejected.
After a crash, an executing call becomes `uncertain` and blocks automatic retry.
This is deliberately conservative: there is no claim that SQLite can atomically commit an
external GitHub effect. An operator must inspect uncertain effects; they are never silently
rerun or declared successful.

Provider 429, 5xx and connection errors receive up to three bounded backoff retries.
Numeric and HTTP-date Retry-After are honored; waits over 120 seconds pause the task.
Exhaustion/authentication failure produces an explicit failed, resumable task, not an empty
successful final. `turn.retry` resumes the saved continuation and tool ledger.
The updated phone offers Retry after failed/paused tasks and on history restore.
Service shutdown pauses running tasks; startup does not silently replay them.
Pending approvals stay pending with their original expiry.

Cancellation stops the server job; a phone disconnect does not. Repeated-identical-call,
consecutive-failure and no-progress guards plus configurable
`JARVIS_DEVELOPER_TASK_TIMEOUT_SECONDS` (default 3600, zero disables emergency deadline)
protect against runaway work. These are per-task safety controls, not billing quotas.
There are no daily, five-hour or weekly API usage allowances.

Completed-response token usage is recorded; incomplete/retried requests may incur additional
provider usage not reported in those totals. No monetary estimates or hard-coded prices are used.

## Credentials and configuration

The Developer service receives only its own bearer token and the API key via systemd credentials.
It does not receive the Core environment. The existing API key can be provisioned once, locally,
without printing it or copying unrelated Core secrets:

```sh
python -m developer_gateway.provision_credential \
  --source-env /path/to/owner-private/approved.env \
  --destination "$HOME/.config/jarvis/developer-api-key"
```

The destination is created exclusively mode 0600; an existing key is never overwritten.
Alternatively provision a dedicated API project/service-account key in that file.
Keep nonsecret settings in `~/.config/jarvis/developer.env`:

```ini
JARVIS_DEVELOPER_BACKEND=api
JARVIS_DEVELOPER_MODEL=gpt-5.4
JARVIS_DEVELOPER_TEST_PYTHON=/path/to/approved/test-venv/bin/python
```

`JARVIS_DEVELOPER_API_KEY_FILE` is available for isolated sidecars; production uses
`LoadCredential=jarvis-developer-api-key`. Health reports configured/healthy booleans,
default backend and bounded job counts, never secrets, prompts or response content.
The journal is private application data, not an operational log. Provider failures are mapped
to fixed safe messages. Tool output and assistant text are credential-redacted before delivery.

## Service installation

The reference unit is `developer_gateway/jarvis-developer.service`. The installer refuses any
source other than a clean, remote-matching `jarvis/unified-production` checkout, copies the
gateway into a commit-addressed runtime release, and atomically points `current` at that release.
The installed `jarvis-developer.service` is enabled at boot, restarts on failure, and reports its
exact source commit from `/health`. Runtime dependencies are in
`developer_gateway/requirements.txt`.

Install/restart **only this gateway**, not Core, when these components change.
The installer preserves the Developer bearer token, private thread database, Codex history,
and Tailscale configuration. Docker is required for the hardened systemd service; the installer
builds its tooling image from dependency manifests only, without repository source or credentials.
Offline build profiles need their dependencies preinstalled; missing
dependencies fail explicitly rather than granting network or host access.

## Certification and limits

Deterministic tests cover provider selection/pinning, streaming/continuation, retries,
durable completed-call replay, crash uncertainty, approvals, cancellation, independent threads,
WebSocket disconnect/reconnect, path/branch restrictions, and real sandbox isolation.
Live sidecar certification must use a separate state directory and never change the live default
until read-only and disposable worktree/test probes pass.

The API sidecar was exercised with the production systemd restrictions (including
`NoNewPrivileges` and a read-only host filesystem). Its read-only task used repository,
search and bounded-file tools and returned a final answer without workspace writes.
Its disposable task created one worktree, applied one patch, ran one passing focused test,
and returned the diff to the model. Disconnect/reconnect preserved the job and final history.
Restarting the sidecar preserved all four completed tool calls and the file hash, with
no duplicate execution. These are server/protocol checks, not physical-device certification.

An initial sidecar verification failed safely because Bubblewrap namespaces were unavailable
under systemd hardening. The service now uses the existing project's restricted Docker
execution pattern; it does not disable `NoNewPrivileges` or fall back to host execution.

API image attachments, automatic reconciliation of uncertain effects, privileged deployments,
and online dependency installation are intentionally unavailable in this release.
Text attachments are supported. Deleting a chat preserves its worktree; cleanup is an explicit
operator action, never a hidden history-deletion side effect.
Physical phone/watch acceptance requires attached hardware and must not be inferred from
server-side WebSocket tests.

Useful checks:

```sh
systemctl --user status jarvis-developer.service
curl http://127.0.0.1:8765/health
journalctl --user -u jarvis-developer.service
```

The authenticated `/ready` and `/metrics` endpoints require the same bearer credential as the
WebSocket. Never paste the credential into issue reports or logs.

## Wireless ADB readiness

`tools/adb_readiness.py` runs on the Ubuntu PC and reconnects already-paired phone/watch
endpoints whenever Android advertises a rotated `_adb-tls-connect._tcp` address. It writes a
mode-0600 status snapshot to `~/.local/state/jarvis/adb-readiness.json` and only reports the
explicit SM-G996B phone and SM-L315F watch models. Install it with
`tools/install_adb_readiness.sh`.

Android intentionally prevents an ordinary application from silently enabling Wireless
debugging. The Developer options switch must remain enabled on each device; the readiness
service handles discovery and reconnection after that point. Jarvis does not enable insecure
TCP/5555, require root or Device Owner provisioning, or use Accessibility workarounds.

## Remaining deployment prerequisite

The phone must be signed into the existing tailnet and its Tailscale VPN must be active for secure
mobile-data access. A tailnet administrator must grant the local Linux user permission to update
Tailscale Serve (or install the provided `/developer` route with sudo). This is an infrastructure
permission, not an application fallback; the gateway intentionally remains localhost-only.
