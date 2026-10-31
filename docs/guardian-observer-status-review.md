# Guardian observer status: namespace-safe, read-only interface

## Scope and reproduced cause

Based on canonical `b09a1de63cb54481972cee8803a5ee6be1b90ed3`.
This change is for review, not permission to install, restart, deploy, enroll,
resume, or certify Phase 1. Guardian and broker implementations, policy, manifests,
hashes, ownership checks, existing units, signed controls and Android are unchanged.

Host `jarvis-autonomy status` returned STOPPED, trusted=true, generation
`0c8b01fb-1347-4d2b-83b7-9a9c1e80ef89`. The running Developer service has
ProtectSystem=strict, PrivateTmp=yes and NoNewPrivileges=yes. Its user namespace
mapping is `1000 1000 1`. A disposable, unprivileged `systemd-run --user` probe with
those three protections reproduced policy-file UID 65534. The existing Guardian
correctly rejects that metadata; accepting UID 65534 in Guardian would be wrong.

The probe connected to the existing system-manager-created operator socket only
to read kernel SO_PEERCRED, without sending any request. Its listening-socket
creator is `(PID=1, UID=0, GID=0)` on the host, and `(1,65534,65534)` in the sandbox.
No signed or unsigned control action was submitted. No production unit restarted.

## Small separate boundary

The proposed system-manager socket activates one root-owned status module. It
loads the **existing installed** Guardian in the host user/PID namespaces with
isolated Python (`-I`), and invokes its existing full `Guardian.status()` validation.
It also checks the installed module and all parent directories are root-owned,
non-writable by group/others, with no symlink or file hardlink substitution.
It does not read status in the Developer namespace, forge trust, or cache trust.

Only the operator UID in the existing trusted Guardian policy may read status.
SO_PEERCRED supplies that identity; no request can supply a UID or an allow-list.
The socket is root:aaron 0660 in a root-owned 0755 directory. Group membership
alone is insufficient: the service verifies the exact kernel UID. The autonomy
account is not granted access. Status access grants **no mutation authority**.

Client authentication requires the listening socket's kernel creator to be host
system manager **PID 1**, with root or namespace-overflow UID/GID. UID 65534 alone
and UID 0 alone are explicitly insufficient. This deployment assumes the existing
host PID namespace and host systemd socket activation, not a container/private PID
namespace, arbitrary root daemon-created socket, user service, or forwarded TCP
connection. A different deployment must fail closed rather than relax this check.

Wire contract: exactly `JGS1` plus a fresh 32-byte challenge, followed by write EOF.
No action, shell text, path, JSON request, signature, or additional bytes are accepted.
The reply echoes that challenge and contains at most 256 bytes of JSON, with only
`state`, `trusted`, `generation`. Total deadlines: server 2 seconds, client 3 seconds.
Two complete Guardian validations must agree before sending. The client requires
the matching challenge, EOF, unique/exact fields, trusted=true, ACTIVE/STOPPED and
a canonical UUID. Missing endpoint, bad credentials, malformed/oversized/late reply,
old challenge, concurrent generation change, and any trust failure yield
`UNKNOWN / false / null`. There is no fallback to cached success or local UID checks.

Freshness is per request, not an authority lease. State may change after a valid
snapshot; every actual signed control still independently checks current Guardian
generation. No numeric ordering is inferred from UUID generations. Replaying an
old response cannot satisfy a new challenge. No request to this status interface
can invoke stop/resume, including an unsigned request naming those actions.

The new service has a read-only filesystem, empty capability sets, no network,
no-new-privileges, no writable exceptions, resource limits and no request/error
logging. Existing Developer ProtectSystem, PrivateTmp and NoNewPrivileges remain
unchanged. Root code is intentionally tiny and has no subprocess/control dispatcher.
The root/system-manager trust boundary is assumed; a compromised root or system
manager cannot be secured by a local status socket. A permitted reader can cause
bounded availability loss, but never a successful fabricated control action.

## Provider-health correction

An additive `api_backend_health` field reports UNKNOWN until a provider response
establishes success or a provider failure establishes UNHEALTHY. Existing Boolean
fields and Android protocol remain compatible. Configuration, startup, a health
HTTP request, or a new backend instance does not establish HEALTHY. Only the
existing completed provider-response path sets success; there is no synthetic probe.

The observer treats configured/false/UNKNOWN and legacy configured/false as missing
evidence, not a failure. Contradictory or invalid classifications are also unknown.
Explicitly unconfigured remains unavailable. Known provider failure remains false;
successful observed provider response remains true. Initial unknown creates no
failure goal and no verified API capability. Existing historical observations keep
their timestamps and ordinary 15-minute freshness limit; this is not a migration
or deletion of prior roadmap evidence. No live provider request was needed or made.

## Future installation boundary — NOT performed

Separate explicit approval is required before installing these new root-owned files:

- `/usr/local/libexec/jarvis-autonomy-status/status.py` (root:root 0644; directory 0755)
- `/etc/systemd/system/jarvis-autonomy-status.service` (root:root 0644)
- `/etc/systemd/system/jarvis-autonomy-status.socket` (root:root 0644)

Systemd would create `/run/jarvis-autonomy-status/status.sock` with the above modes.
No Guardian/broker payload or manifest may be replaced to install this independent
reader. Installation must refuse unknown/modified destination payloads, validate
the installed Guardian's trusted version and operator UID/group, verify the units,
and activate only the new socket. No privileged installer is executed by this PR.
The two service unit files are the reviewed deployment specification, not a claim
that privileged installation or an upgrade rehearsal has occurred.

After approval, first validate host and actual Developer-sandbox queries against
the unchanged Guardian CLI, then deploy the observer client separately. Until the
new socket exists the new client deliberately returns UNKNOWN/NEEDS ATTENTION.
Live root-service validation, system-manager socket end-to-end validation, and
physical dashboard acceptance remain outstanding; root-free tests cannot certify
those steps. STOPPED must be retained throughout. Removing/disabling only this new
socket/service rolls status reporting back to UNKNOWN, not to an unsafe fallback.

## Reproducible validation

- Focused Guardian status, peer credentials, tamper, generation changes, wire framing,
  provider classification, observer/API and signed-control tests: 93 passed locally.
- Root-free tests exercise actual unchanged Guardian validation on disposable state;
  socketpair tests use kernel caller credentials. Client PID1/remapping tests inject
  kernel credential tuples; the separate live read-only namespace probe verifies
  those tuples on this host. Neither is a claim that the new root service is installed.
- Provider transition tests use mock completed/error provider responses, not paid
  provider traffic. Existing signed control/replay tests remain separate.
- No source under Android, release workflows, Guardian/broker implementations or
  manifests is modified. No credentials, journal contents or private conversations
  are collected in this report.

Full local Python suite: **2,411 passed, 3 skipped, 143 subtests passed** in 48.72s.
The three existing end-to-end deployment/rollback gates were not activated. The
sole warning is the existing Starlette/httpx test-client deprecation. Python is
3.14.4 locally; protected CI Python 3.12 is separately required, not inferred.

Ruff correctness and format: 275 files accepted. Mypy: 121 active source files plus
the new status module accepted. Python compilation, product baseline and high-severity
repository Bandit passed. Full-severity Bandit on the status service/client passed.
Dependency audit reported no known vulnerabilities. Pinned CI Gitleaks digest
`c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f` scanned a
disposable tracked/new-source snapshot with CI's current-tree, redacted settings:
zero findings. Pinned actionlint passed all four unchanged workflows (explicit
workflow filenames are needed because the disposable snapshot has no `.git`).
Systemd unit verification passed; the only warnings concern unrelated host XFS
units' obsolete CPUAccounting option. No new unit hardening was removed.

The pre-push security baseline has 24 existing canonical alerts in Core
`ai_engine.py`/`dialogue_manager.py` (21 high ReDoS, 3 medium log injection). They
are not modified or dismissed by this change. PR-specific CodeQL analysis and
alert-gate results must be recorded against the exact pushed head. Branch protection
requires core-tests, quality-security, android-unit-tests and Analyze Python with
strict up-to-date checks and administrator enforcement. Feature pushes/PRs cannot
trigger signing: public signing is `v*`-tag-only, private signing is manual dispatch.

Local logs: `/tmp/jarvis-guardian-status-evidence.wmoz1x/`. Exact-head GitHub results
are recorded in the PR evidence after completion. No signing, release, installation,
provider traffic, production restart or autonomy activation is part of validation.
