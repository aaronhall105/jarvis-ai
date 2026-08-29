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
- Codex uses `workspace-write` with `on-request` approvals. The Android action grants matching
  safe commands for the current development session, avoiding repeated prompts while retaining
  approval for new or higher-impact operations. Requests are displayed as
  native Android confirmation sheets.
- Events are filtered to threads owned or resumed by that authenticated WebSocket connection.
- Requests are rate limited. Audit logs use fixed operation-event names and never include
  user-supplied workspace values, prompts, tokens, file contents, or command output.
- Destructive actions remain subject to Codex approvals and the existing repository rules.

## Service installation

The reference unit is `developer_gateway/jarvis-developer.service`. The installer refuses any
source other than a clean, remote-matching `jarvis/unified-production` checkout, copies the
gateway into a commit-addressed runtime release, and atomically points `current` at that release.
The installed `jarvis-developer.service` is enabled at boot, restarts on failure, and reports its
exact source commit from `/health`. Runtime dependencies are in
`developer_gateway/requirements.txt`.

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
