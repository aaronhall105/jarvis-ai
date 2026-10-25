# Protected broker: operator installation review

## Post-install public status correction

The first installed bundle incorrectly included the private Docker configuration
in the public Guardian file list. Non-root status consequently failed closed even
though protected hashes were correct. Do not make the file readable or loosen
directory permissions to fix this.

The explicit `--repair-public-manifest` installer mode performs only the known
metadata repair: remove that one direct public entry, retaining its hash in the
broker manifest and the broker manifest's hash in Guardian. The root broker still
verifies its private file before serving. The mode requires local Aaron/root,
STOPPED, exact installed payload, private file hash, and valid protected audit.
It records repair hashes and rolls back metadata on failure. It does not reinstall
payload, restart services, change permissions/policy, enroll keys or enable autonomy.
This repair is currently prepared/tested but not yet applied to the live host.

## Original installation review

This upgrades the Guardian-only host. It does not enable autonomy. It grants no
Docker group, sudo, root shell, arbitrary commands, secret access, Guardian
modification or self-resume authority to the autonomous account. Normal Jarvis
and manual Developer Mode are not restarted or changed.

## Supported boundary and deliberate limits

- Registered job-owned workspaces; immutable snapshots; pinned offline
  Python/security verification profiles. Network disabled, read-only root,
  unprivileged UID, dropped capabilities, no-new-privileges, resource/time/output
  limits. No host home, Docker socket, or credentials in the sandbox.
- Offline Core candidate build from exact public Git blobs and pinned base image;
  no request-supplied Dockerfile/command, executable, image or flags.
- Exact merged PR/head, protected check/workflow provenance, independent review,
  clean canonical checkout and artifact/source validation before release.
- Actual fixed Core Compose deployment path, existing mounts. Only static assets
  may change; executable/persistence/dependency blobs must match pinned baseline.
- Software rollback restores recorded prior image/SHA, NOT user data. Private
  temporary read-only DB/WAL samples verify schema/health and are never exposed
  to development tools. Unknown migrations deny deployment; uncertain rollback
  requires reconciliation rather than repeated effects.
- Separate worker/operator sockets; signed operator identity, epoch, expiry and
  durable replay protection. STOP does not wait behind a sandbox job.

**Initial reviewer identities and remote operator public-key enrollment are empty.**
Release admission and remote signed controls stay denied until separately
provisioned. No mobile/Core bearer token alone grants resume. Existing local host
STOP/RESUME remains. Governor/API Developer client wiring and Android UI are later
phases. This is not a full-autonomy certificate.

Developer gateway, Android/Wear deployment, Git mutations, canonical checkout
updates and arbitrary services are not broker operations. Unsupported requests
are rejected; no placeholder deploy path is accepted.

## Exact root-owned files

Payload files 0644; directories 0755:

```text
/etc/jarvis-autonomy/broker.json
/usr/local/libexec/jarvis-autonomy-broker/entry.py
/usr/local/libexec/jarvis-autonomy-broker/deploy_unified_core.sh
/usr/local/libexec/jarvis-autonomy-broker/tools/__init__.py
/usr/local/libexec/jarvis-autonomy-broker/tools/autonomy_broker.py
/usr/local/libexec/jarvis-autonomy-broker/tools/autonomy_broker_data.py
/usr/local/libexec/jarvis-autonomy-broker/tools/autonomy_broker_evidence.py
/usr/local/libexec/jarvis-autonomy-broker/tools/autonomy_broker_host.py
/usr/local/libexec/jarvis-autonomy-broker/tools/autonomy_broker_ipc.py
/usr/local/libexec/jarvis-autonomy-broker/tools/autonomy_broker_operator.py
/usr/local/libexec/jarvis-autonomy-broker/tools/autonomy_broker_release.py
/usr/local/libexec/jarvis-autonomy-broker/tools/autonomy_broker_runtime.py
/usr/local/libexec/jarvis-autonomy-broker/tools/autonomy_broker_snapshot.py
/usr/local/libexec/jarvis-autonomy-broker/tools/autonomy_broker_store.py
/usr/local/libexec/jarvis-autonomy-broker/tools/autonomy_guardian.py
/etc/systemd/system/jarvis-autonomy-broker-worker.service
/etc/systemd/system/jarvis-autonomy-broker-worker.socket
/etc/systemd/system/jarvis-autonomy-broker-operator.service
/etc/systemd/system/jarvis-autonomy-broker-operator.socket
/etc/systemd/system/jarvis-autonomy.service.d/broker.conf
```

Only the two broker sockets are enabled/started, creating their corresponding
links under `/etc/systemd/system/sockets.target.wants/`. The existing autonomy
worker is NOT enabled/started. No Core, Developer, Docker or unrelated restart.

Protected persistent paths:

```text
/var/lib/jarvis-autonomy-broker/                       0755
/var/lib/jarvis-autonomy-broker/installed.json         0644
/var/lib/jarvis-autonomy-broker/records/               0700
/var/lib/jarvis-autonomy-broker/snapshots/             0700
/var/lib/jarvis-autonomy-broker/worker/                0700
/var/lib/jarvis-autonomy-broker/operator/              0700
/var/lib/jarvis-autonomy-broker/docker-client/         0700
/var/lib/jarvis-autonomy-broker/docker-client/config.json 0600 (empty config)
/var/lib/jarvis-autonomy-jobs/                        0755
```

Private ledger/lock/audit/receipt files are created beneath those paths (0600).
Only a validated job subdirectory belongs to the isolated autonomy UID; the
account cannot write the protected parent.

Existing `/var/lib/jarvis-autonomy-guardian/installed.json` is atomically extended
with broker hashes. Existing Guardian executable, policy, account, sudoers, unit
and history remain. STOP refreshes `state.json` and appends an audit event; it
does not delete work or enable autonomy. No credentials are copied.

Runtime sockets:

```text
/run/jarvis-autonomy-broker/worker.sock    root:jarvis-autonomy 0660
/run/jarvis-autonomy-broker/operator.sock  root:aaron           0660
```

## Installer, repeat and recovery

`tools/install_autonomy_broker.sh --check` is non-mutating. It checks trusted
STOPPED Guardian, isolated account, host tools/images, actual runtime layout,
canonical source/cleanliness and absent conflicting destinations.

Privileged invocation requires Aaron's local sudo identity, verifies protected
Guardian audit, uses exclusive file creation and retains STOPPED. Repeat verifies
and retains pinned policy; it never silently expands permissions or updates pins.
Changed bundles, unknown/tampered files and partial installs are refused.

On activation failure only new broker sockets/services are stopped. When safe,
known new payload is removed and original Guardian manifest restored. Nonempty
audit/ledger evidence is preserved. Unconfirmed cleanup leaves files for explicit
reconciliation, never automatic overwrite/replay.

Host `jarvis-autonomy stop` remains independent of Internet, models, Core and
Android. For retirement, first STOP, then operator-disable only the two broker
sockets and stop only their two services. Do not delete files still covered by
Guardian hashes or erase audits/jobs. Manifest/payload retirement requires a
reviewed recovery procedure; there is intentionally no autonomous uninstall.

## Validation boundary

261 focused root-free tests passed, including disposable-filesystem upgrade,
repeat/tamper/partial failure, IPC, release provenance, recovery, STOP and rollback.
Ruff/format, mypy, syntax, shell, systemd, temporary sudoers syntax and repository
Bandit gate passed. Checksum-verified Gitleaks 8.30.1 found no source secrets.
Host XFS CPUAccounting warnings are unrelated; no host units changed.

Canonical Git inspection uses only an exact process-local safe.directory for the
fixed operator-owned worktree. This permits root inspection without inheriting
SUDO_UID/global Git settings; it does not allow dirty source. Real-Git tests cover
foreign-owner behavior and tracked/staged/untracked rejection. No global trust
exception is installed. Git execution failures are distinct from dirty-source
failures; neither is ignored.

Rehearsal simulates root ownership/systemd over real temporary files. Actual
host installation, UID isolation, sockets, negative IPC, ordinary Jarvis health
and phased live release remain to be certified after Aaron confirms installation.
No Android physical or full-autonomy certification is claimed.
