# Autonomy Guardian: operator installation review

Status: local feature work on `jarvis/autonomous-development`, based on
`429cee8ac2d8efdc5d7c99805699edae78e9a85d`. This is the host interlock bootstrap,
**not production certification of autonomous R&D**. No privileged installation
has been performed by the coding agent.

## What this installs

- A dedicated system account/group, `jarvis-autonomy`, with a locked default
  password, `/usr/sbin/nologin`, and no supplementary groups. It does not inherit
  Aaron's home, sudo, Docker, LXD, or credentials.
- A root-owned Guardian independent of Core, Developer, models and networking.
- A persistent root-controlled STOPPED/ACTIVE state and private append-only-by-
  implementation security journal. Only root can rewrite these files; this is
  not cryptographic protection against a malicious host administrator.
- A root-owned, sandboxed worker unit running as that restricted account. The
  worker is **not enabled or started**, and no Governor is installed by this step.
- `jarvis-autonomy status`, `stop`, and `resume`. Only Aaron receives narrowly
  matched passwordless sudo entries for the two fixed control operations. There
  is no sudo grant to the autonomy account.

Only the protective Guardian service is enabled at boot and started. Installation
does not enable autonomous research, coding, PRs, deployment, or API usage.
Core, the manual Developer service, Tailscale, Android, GitHub, existing emergency
stops, integrations, release metadata and all credentials are left untouched.

## Exact host changes

All files/directories below are root-owned unless explicitly noted. Installation
refuses pre-existing conflicting files, symlinks, directories or accounts.

| Path | Mode / purpose |
| --- | --- |
| `/usr/local/libexec/jarvis-autonomy/` | 0755, protected helper directory |
| `/usr/local/libexec/jarvis-autonomy/guardian.py` | 0644, fixed-path controller |
| `/usr/local/libexec/jarvis-autonomy/control` | 0755, isolated Python launcher |
| `/usr/local/bin/jarvis-autonomy` | 0755, operator command |
| `/etc/jarvis-autonomy/` | 0755, protected configuration directory |
| `/etc/jarvis-autonomy/guardian.json` | 0644, operator UID and deny-all privileged-operation policy |
| `/etc/sudoers.d/jarvis-autonomy` | 0440, Aaron's exact stop/resume commands only |
| `/etc/systemd/system/jarvis-autonomy-guardian.service` | 0644, root Guardian |
| `/etc/systemd/system/jarvis-autonomy.service` | 0644, restricted worker, not enabled |
| `/etc/systemd/system/multi-user.target.wants/jarvis-autonomy-guardian.service` | systemd enablement symlink to the root unit |
| `/var/lib/jarvis-autonomy-guardian/` | 0755, protected state directory |
| `/var/lib/jarvis-autonomy-guardian/installed.json` | 0644, installed payload hashes and bundle identity |
| `/var/lib/jarvis-autonomy-guardian/state.json` | 0644, atomic persistent state and revocation generation |
| `/var/lib/jarvis-autonomy-guardian/control.lock` | 0600, serialized control operations |
| `/var/lib/jarvis-autonomy-guardian/audit/` | 0700, protected audit directory |
| `/var/lib/jarvis-autonomy-guardian/audit/events.jsonl` | 0600, content-free security events |
| `/var/lib/jarvis-autonomy/` | 0755, root-owned account home boundary |
| `/var/lib/jarvis-autonomy/work/` | 0700, **owned only by jarvis-autonomy**, future work area |

`useradd` also updates the normal system account databases (`passwd`, `shadow`,
`group`, `gshadow`, and their standard backups where applicable). Temporary
root-only installer staging under `/run/jarvis-guardian-install-*` is removed
when installation exits. systemd writes its ordinary journal/runtime metadata.
No API key, GitHub credential, Developer token or Docker socket is copied.

## Security contract and current limits

The installed Guardian executes only fixed systemctl operations for its own
worker and a fixed `pkill` of the validated dedicated UID. It cannot execute a
caller-supplied program, unit, path, release, script, or shell command. Python
runs with `-I`; no privileged runtime imports come from a Jarvis worktree.

STOP writes STOPPED before terminating the autonomous worker/UID. Audit or lock
failure still attempts termination. The watch service checks the state every
second without network/model calls. Corrupt/missing state or changed protected
payloads fail closed. The worker is bound to the Guardian's service lifetime.
Guardian restart/reboot explicitly returns to STOPPED; it never auto-resumes.
Control operations are serialized, and subprocess waits are bounded. This is
not a claim of zero-latency cancellation or reversal of an already-accepted
external side effect. Later brokers must recheck state/generation at admission
and reconcile uncertain results rather than replaying them.

Resume requires root/operator authority, intact files, the isolated account,
the active Guardian and an installed Governor entry point. A failed/exceptional
start restores STOPPED and terminates the worker. **Resume cannot start useful
autonomy yet**, because the Governor has not been installed or certified.

Privileged operations are currently an empty allowlist. There is deliberately
**no deployment, root shell, Docker, credential, or release broker**. This step
does not complete the original narrow autonomous release-authority requirement.
Adding such authority requires a separately reviewed implementation; ordinary
product code must never update the Guardian or widen this policy. Do not promise
that this bootstrap alone eliminates every future privileged installation.
Android operator authentication/resume, broker enforcement, API jobs, roadmap,
autonomous merge/deploy/rollback and dashboard work remain subsequent work.

The worker has no sudo/Docker groups, no capabilities, `NoNewPrivileges`, a
read-only host filesystem apart from its work directory, hidden host homes and
Docker/system D-Bus sockets, private temporary/devices, and bounded resources.
Account validation rejects *any* extra group, not just a short blacklist.
Actual kernel/DAC/systemd enforcement must be tested after installation;
unprivileged unit tests do not substitute for that proof.

## Review and install

Run the root-free plan check if desired:

```sh
/home/aaron/jarvis-autonomous-development-worktree/tools/install_autonomy_guardian.sh --check
```

The check emits a content-free bundle hash and planned paths without changing
the host. Review this document, the two unit files and `tools/autonomy_guardian*`.
The installer uses exclusive creation, validates sudoers before activation,
records installed hashes, refuses conflicting state, and never silently upgrades
a different Guardian bundle. Re-running the identical intact installation is
safe and leaves autonomy STOPPED. Partial failure requires operator inspection;
it never overwrites conflicts or deletes evidence to make a retry succeed.
During installation it also runs a read-only check as the real restricted UID:
protected paths must not be writable, the audit must not be readable, Docker
sockets must not be writable and non-interactive sudo policy listing must not
succeed. No resume or privileged deployment is attempted in this check.

The one local installation command is:

```sh
sudo /home/aaron/jarvis-autonomous-development-worktree/tools/install_autonomy_guardian.sh
```

Run this locally. Your computer may ask for your Linux password. Type it into
the local terminal only. Do not send it to the coding agent. Confirm completion;
there is no need to share output unless installation fails. Never share secrets.

## Post-install certification

### Operator installation and independent follow-up, 2026-10-22

Aaron reported successful local installation. The coding agent did not rerun the
installer or alter installed protected files. Independent host observations:

- Guardian service active/enabled, zero service restarts; worker inactive/static.
- `status` and `validate` report trusted STOPPED, with installed payload hash
  validation enabled. Separate invocations reload the same persistent state.
- Dedicated UID 995, GID 980, sole group `jarvis-autonomy`; no sudo, Docker,
  Aaron or other supplementary group membership.
- Helper directory/files, policy, state, manifest and both system units owned
  by root with the documented modes. Audit directory root-owned 0700. Work
  directory owned by the dedicated account, mode 0700.
- Aaron's exact installed STOP control succeeded. RESUME was rejected because
  the Governor is not installed. A subsequent STOP succeeded; final STOPPED.
  This tests readiness rejection, **not successful ACTIVE/resume certification**.
- Readable Guardian journal contained one record, no error-priority entries and
  no fixed Guardian-operation-failure entry. This is not a full secret scan of
  private audit storage.
- Installed policy still has `privileged_operations: []`. No release helper is
  installed. No protected policy was expanded to bypass that restriction.
- Root-only audit-chain reading and executing the real dedicated-UID validation
  command were attempted non-interactively and denied because administrative
  authentication was required. No password was requested. Actual audit-chain
  verification, dedicated-UID denial tests and a real Guardian service restart
  remain **unverified in this follow-up**; do not substitute fixture tests for
  those live checks. The successful installer report is evidence that its own
  restricted-UID preflight ran, but not an independent rerun.
- 62 existing Guardian tests passed again, covering corrupt-state fail-closed,
  mocked restart/STOP persistence, authorization and audit-chain fixtures.
- Core liveness healthy at `8b8d7b08160e3ea36f9d944e2ab2528b37dddc0a`;
  Developer healthy at `429cee8ac2d8efdc5d7c99805699edae78e9a85d`, API default,
  API configured/healthy and Codex available. Database, Home Assistant, Task
  Centre, follow-up and email-policy workers reported healthy. Realtime enabled
  and configured; speculation false. No normal service was restarted.
- Core readiness briefly returned degraded with OpenAI web search unavailable;
  a subsequent provider snapshot reported web search healthy. No causal link
  to Guardian installation was established. Owner-authenticated Microsoft,
  proactive and physical-device acceptance were not independently certified.
- Release remains `19.0.0-alpha39`; canonical worktree clean at `429cee8...`.
  No integration reconnection, credential change, PR or remote branch creation.

The next privileged boundary is not ordinary development approval: a reviewed
release/test broker and operator-authenticated Android control path must be
implemented and installed before autonomous engineering/release certification.
The current immutable bootstrap cannot acquire those operations itself. A
separate explicit operator decision is required for another protected host
installation; do not silently reuse Aaron's Docker or manual Developer authority.

### Earlier root-free checkpoint

Root-free checkpoint, 2026-10-22: 62 Guardian tests and 12 existing Developer
gateway/auth tests passed (74 total). The tests cover an isolated installer
rehearsal, same-bundle reinstallation, conflict/tamper refusal, state reload,
unauthorized resume, corrupt-state rejection, startup exceptions, stop cleanup,
audit chaining, group/path controls and fixed privileged arguments. Gateway
tests were rerun with a disposable state directory: their initial default-path
run correctly hit the live service's exclusive-state lock. No live state was
taken over or service stopped.

Ruff, format, mypy, compileall, shell syntax, `visudo -cf` and systemd unit
verification passed. Bandit passed the existing repository high-severity gate
(`-lll`); low-severity subprocess advisories were reviewed against the fixed
absolute commands and lack of shell execution, not suppressed. systemd also
reported unrelated host XFS `CPUAccounting` deprecation warnings. Live Core and
Developer health were healthy; speculation remained false. No privileged
installation, real restricted-UID validation or physical-device certification
has yet occurred.

Verify exact root ownership/modes/hashes, private audit, account UID/home/shell,
group isolation and absence of Docker/sudo access. Verify denied writes/resume
under the actual dedicated UID, successful operator status/stop, and readiness-
gated resume. Verify STOP survives Guardian restart/state reload, missing/corrupt
state is interpreted as STOPPED, and no untrusted worker can detach around the
service/UID fence. Perform corruption checks only in an isolated test fixture,
not by editing live protected state. Verify normal Core/manual Developer health.

Keep autonomy STOPPED until these checks and the original observe-only,
disposable-worktree, PR, canary, rollback and full-autonomy phases pass.

## Emergency control and operator recovery

`jarvis-autonomy stop` works locally without Core, Developer, Internet, Android,
OpenAI or Home Assistant. `jarvis-autonomy status` is read-only. `resume` requires
Aaron and the runtime readiness checks; the autonomy identity cannot resume.

If the wrapper is damaged, an operator can use the fixed root control directly
or stop/disable the two named system services using local administrative access.
Never stop Core or manual Developer as part of autonomy recovery. STOP retains
roadmap, history, worktrees and audit evidence.

For operator-reviewed removal: first stop autonomy, then disable/stop
`jarvis-autonomy.service` and `jarvis-autonomy-guardian.service`. After verifying
the dedicated UID has no running processes, remove only the two named systemd
unit files, their Guardian enablement symlink, the two libexec payload files,
`/usr/local/bin/jarvis-autonomy`, and `/etc/sudoers.d/jarvis-autonomy`; then reload
systemd and validate sudoers. Use explicit reviewed file paths, not recursive
directory deletion. Preserve `/etc/jarvis-autonomy`, both `/var/lib/jarvis-*`
directories, the locked account, audit/history/worktrees and installation
manifest for reconciliation. Reinstallation after partial removal intentionally
refuses until an operator reviews the retained state. No autonomous uninstall
or permission-expansion operation exists.
