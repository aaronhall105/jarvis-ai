# Jarvis Developer mode

Developer mode is a second, explicitly selected backend in the existing phone chat UI. Normal
Jarvis messages continue to use Jarvis Core. Developer messages use an authenticated gateway on
the Ubuntu Jarvis PC, which owns the official Codex App Server process and its persistent threads.

## Architecture

The gateway runs `codex app-server --stdio` as one warm child process. It translates the small
Jarvis phone protocol into App Server thread and turn requests and forwards structured App Server
events. It never exposes a shell protocol. Only the allow-listed `jarvis` and `jarvis-wear`
workspaces can be selected.

The gateway binds to `127.0.0.1:8765`. Remote access is intended to use Tailscale Serve HTTPS/WSS
at `/developer`; the raw port must not be exposed publicly. The Android credential is separate
from Jarvis Core credentials and is encrypted with Android Keystore. The Ubuntu token is loaded
from a systemd credential backed by a mode-0600 file.

The phone resumes the persisted App Server thread after reconnecting. Codex work continues on the
PC if the Android UI disconnects, and the session can be resumed after reopening the app. Network
callbacks reorder local and secure-remote endpoints and reconnect with bounded exponential
backoff. The phone microphone remains off unless the user explicitly starts voice input.

## Security model

- Authentication fails closed when the gateway token is absent.
- TLS is terminated inside the private Tailscale network; no public command socket is created.
- Workspaces are selected by fixed identifier, then canonicalised and checked against the
  allow-list.
- Codex uses `workspace-write` and `on-request` approvals. Approval requests are displayed as
  native Android confirmation sheets.
- Events are filtered to threads owned or resumed by that authenticated WebSocket connection.
- Requests are rate limited. Audit logs record operation types and workspace IDs, not prompts,
  tokens, file contents, or command output.
- Destructive actions remain subject to Codex approvals and the existing repository rules.

## Service installation

The reference unit is `developer_gateway/jarvis-developer.service`. The installed user service is
`jarvis-developer.service`; it is enabled at boot, restarts on failure, and logs to the user
journal. Runtime dependencies are in `developer_gateway/requirements.txt`.

Useful checks:

```sh
systemctl --user status jarvis-developer.service
curl http://127.0.0.1:8765/health
journalctl --user -u jarvis-developer.service
```

The authenticated `/ready` and `/metrics` endpoints require the same bearer credential as the
WebSocket. Never paste the credential into issue reports or logs.

## Remaining deployment prerequisite

The phone must be signed into the existing tailnet and its Tailscale VPN must be active for secure
mobile-data access. A tailnet administrator must grant the local Linux user permission to update
Tailscale Serve (or install the provided `/developer` route with sudo). This is an infrastructure
permission, not an application fallback; the gateway intentionally remains localhost-only.
