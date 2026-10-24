# Protected autonomy broker — current recovery checkpoint

Updated 2026-10-24. **Operator has installed broker AND applied the public-manifest
repair. Live status now STOPPED/trusted=true.** No reinstall is needed. Preserve
all local WIP. Current product work is tracked in
[Governor checkpoint](autonomy-governor-progress.md). Historical evidence below
describes the defect before repair; it is not a request to repair again.

## Current post-install checkpoint — read this first

Aaron confirmed the protected broker installed successfully. Independently:

- Root-owned broker files, units, sockets, policy and state directories exist.
- Installed payload bundle exactly matches local payload:
  `a85bd8e7e907b38a51ce10e724c13d9bb9744c87e1e5ba9ce5396c1669b9133f`.
- 27 public Guardian-manifest files and 20 public broker-manifest files match
  hashes/ownership/modes. The only inaccessible entry in each is the intentionally
  private `/var/lib/jarvis-autonomy-broker/docker-client/config.json`.
- Worker socket root:jarvis-autonomy 0660; operator socket root:aaron 0660.
  Account UID995 has only jarvis-autonomy group. Aaron cannot connect to the
  worker socket (DAC denial). No attempt was made to acquire UID995/root authority.
- Live malformed operator request returned request_denied. Operator service is
  running/success, demonstrating root-side startup/integrity/ledger initialization.
- Core healthy at 8b8d7b0..., Developer healthy at 429cee8..., API healthy/configured;
  voice speculation false, zero active sessions. Canonical worktree still clean.
- No installer rerun, privileged repair, service restart, activation, Governor
  execution, production change or integration change occurred in this session.

### Reproduced defect and narrow repair

Unprivileged `jarvis-autonomy status` reports STOPPED/trusted=false because the
public Guardian manifest directly includes the correctly private Docker config.
This was reproduced by a failing test before correction. It is NOT proof that
the protected files were tampered with or that the root Guardian is inactive.

Installer fixed for future installs: keep the private config's hash in the broker
manifest, and keep the entire broker manifest pinned by Guardian, but do not
require public Guardian callers to read that private file directly.

Existing host needs one explicit local metadata-only repair:
`tools/install_autonomy_broker.sh --repair-public-manifest`.
The mode requires local root/Aaron authority, exact installed bundle/hashes,
isolated UID, valid audit, STOPPED state and known empty private config. It removes
exactly one direct entry from the Guardian manifest. It does NOT reinstall,
restart, chmod, change policy, remove private broker integrity, copy credentials
or enable autonomy. Requested/completed repair hashes go to protected record
audit; failure restores the original manifest; repeated repair is a no-op.

Only installer source/tests/docs changed; protected payload hash is unchanged.
268 focused tests cover original reproduction, repair exactness/idempotency,
active-state refusal, private-payload tamper refusal, caller rejection and audit
failure rollback. Root repair has NOT been executed by the assistant.

STOP here for operator repair. Do not proceed to Governor/Phase 1 or ask for
activation until public Guardian trust and remaining live safety checks pass.
Direct UID995 negative tests, broker restart persistence and protected private
audit inspection are still outstanding; metadata/synthetic proofs are not those
live tests. Remote keys/reviewer enrollment remains empty as previously stated.

The sections below retain earlier evidence and are superseded by this checkpoint.

## Operator-install refusal investigated and corrected

Aaron's authenticated sudo attempt stopped during precheck with
`canonical_worktree_not_clean_current`; it did not install the broker.
Read-only forensics found NO dirty filenames: tracked, staged, untracked,
porcelain-v2 and diff outputs were empty. Canonical branch and HEAD were correct.
No generated files, unrelated WIP, or files needing restoration were found.
Nothing was deleted, restored, stashed or reset.

Root cause: scrubbed Git environment intentionally omits SUDO_UID/global config.
Root therefore refuses Aaron-owned canonical Git as dubious ownership. The old
adapter mislabeled every nonzero Git exit as source dirtiness. Git's own
GIT_TEST_ASSUME_DIFFERENT_OWNER=1 reproduced exit128 against the actual canonical
worktree without sudo or host mutation.

Fix: verify canonical directory is operator-owned and not a symlink; pass one
process-local safe.directory for the fixed canonical path (no wildcard/global
configuration). Preserve all branch/SHA/tracked/staged/untracked cleanliness gates.
Git execution failures now report canonical_git_query_failed separately.

261 focused tests pass, including eight real-Git disposable-repository cases:
foreign-owner rejection/recovery, clean/no writes, tracked/staged/untracked
rejection and preservation, wrong SHA/branch, wrong owner, execution-error
classification, and runtime/validation paths outside canonical source.
Ruff/format, mypy, compile, Bandit and diff checks pass. Read-only host precheck
passes even with different-owner simulation. Guardian remains trusted STOPPED.
Latest payload fingerprint superseding the earlier one below:
`a85bd8e7e907b38a51ce10e724c13d9bb9744c87e1e5ba9ce5396c1669b9133f`.

## Latest completed work (supersedes historical checkpoint below)

- Worktree `/home/aaron/jarvis-autonomous-development-worktree`, branch
  `jarvis/autonomous-development`, HEAD/base
  `429cee8ac2d8efdc5d7c99805699edae78e9a85d`. No push, PR, install or deployment.
- Production dispatcher now registers isolated jobs, validates snapshots/source,
  runs fixed offline Python/security profiles, builds pinned Core candidates,
  persists artifact/release records and connects separate worker/operator sockets.
- Core release uses a root-owned reviewed copy of the existing deploy entrypoint
  in fixed prebuilt mode, actual `jarvis` Compose project and persistent mounts.
- Data-safe compatibility admits only Core static-asset changes. Executable and
  persistence/dependency blobs must be unchanged. Unknown migrations deny deploy.
  Before/after schemas and health checked; rollback restores only prior software
  image/SHA, never databases/user state. Failed compensation requires reconciliation.
- Chained protected audit, exact-once receipts, signed operator controls, crash
  cleanup of only ledger-owned test containers; interrupted releases never replay.
- Upgrade installer and disposable filesystem rehearsal complete: Guardian-only
  upgrade, repeat, tamper/unknown/partial refusal, activation failure recovery,
  preserved original Guardian state/manifest/audit, no group/credential changes.
- **253 focused tests passed** in 1.00s. Ruff/format (25 files), mypy (12 broker
  sources), compileall, shell syntax, high-severity Bandit gate, systemd validation
  and git diff check passed. Temporary-policy visudo test passed in Guardian suite.
- Systemd CPUAccounting warnings originate in host XFS units, not Jarvis units;
  no host units changed.
- Gitleaks 8.30.1 archive checksum verified. CI-style source-only scan (tracked
  files plus intended untracked WIP, no ignored/runtime data) passed. One false
  positive was the Python class `ec.SECP256R1` next to variable `key`; renamed
  variable to `verifier`, with no validation/rule/allowlist weakening.
- Real-host non-mutating precheck passed: UPGRADE_GUARDIAN_ONLY, Guardian trusted
  STOPPED, UID995 only jarvis-autonomy group, pinned existing images, host tools,
  Python crypto/SQLite, canonical clean/current, runtime layout, no conflicting paths.
- Final reviewed payload fingerprint:
  `da871ec61562bb487313787fc22270a2cfafc5594990338abe97d162cc39abaf`.
- Core healthy at `8b8d7b08160e3ea36f9d944e2ab2528b37dddc0a`, Developer healthy at
  canonical SHA with API selected/configured/healthy; speculation false. No voice
  session interrupted. No full repository suite, root/Docker test execution or
  production restart performed.

## Important limits and next action

- Initial policy has no enrolled remote operator keys or independent reviewer
  IDs. Remote controls and release admission remain DENIED until provisioned.
  Existing local host controls remain authoritative. No self-resume authority.
- Only Python/security verification and compatible Core asset release adapters.
  Developer/Android/Wear deployments and arbitrary operations are rejected.
- Git/PR/check provenance is verified, but autonomous push/PR/merge, canonical
  fast-forward, Governor/API Developer client integration and Android UI are future
  phases. This installation is protected infrastructure, not full autonomy.
- Private Guardian audit chain is additionally verified by the local root
  installer before writes; this session did not read its private contents.
- Installer: `tools/install_autonomy_broker.sh`. Exact paths, limits and recovery:
  [installation review](autonomy-broker-installation.md).
- After giving the reviewed one-line install command, STOP and await Aaron's
  confirmation. Do not assume install, enable autonomy, push/merge, or start UI work.
- Scanner: `/tmp/jarvis-broker-gitleaks.tjOWJA/gitleaks`, archive/checksum alongside.
  Source snapshot: `/tmp/jarvis-broker-source-scan.ZIBjAs`. Final documentation
  included in successful scan (8.52 MB); old redacted finding report is not the
  final scan result.

---

# Historical checkpoint (retained; superseded above)

This is unfinished local WIP, not an installation or production certificate.
Do not run the original Guardian installer to install the broker. No second
upgrade installer has passed its required rehearsal yet. Do not enable autonomy.

## Source and installed state

- Worktree: `/home/aaron/jarvis-autonomous-development-worktree`
- Branch: `jarvis/autonomous-development`
- Base: `429cee8ac2d8efdc5d7c99805699edae78e9a85d`
- No commits, pushes, PRs, privileged installation, Docker execution or service
  restarts were performed during this continuation.
- Installed Guardian remained trusted and STOPPED. UID 995 has only the
  `jarvis-autonomy` group. The installed Guardian is unchanged by source edits.
- Core remained healthy at `8b8d7b08160e3ea36f9d944e2ab2528b37dddc0a`;
  Developer gateway remained healthy at the base SHA, API selected.
- Speculative reads remained false. A live voice session was present at the last
  read-only check; it was not interrupted.

## Recovered and extended implementation

The prior baseline was 142 passing tests: 62 Guardian and 80 broker tests. The
current focused suite has **212 passing tests** (root-free). Reuse these tests;
do not recreate the architecture on resumption.

| File | Implemented behavior |
| --- | --- |
| `tools/autonomy_broker.py` | Strict schemas, peer identity, generation fence, protected ledger/audit, request binding, uncertain replay denial, fixed sandbox argv. Completed receipts can be retrieved after expiry/STOP without re-execution. |
| `tools/autonomy_broker_snapshot.py` | Descriptor-relative bounded snapshots; link/owner/device checks; no Git metadata or environment example copied; protected snapshot materialization. |
| `tools/autonomy_broker_host.py` | Public read-only GitHub transport, exact Git blob verification, private Docker transport, bounded subprocess execution, fixed offline Core context, container transition/compensation harness. |
| `tools/autonomy_broker_evidence.py` | Exact PR/head/merge provenance, full check-page requirements, check-to-workflow origin binding, pinned protected source, independent exact-head review. |
| `tools/autonomy_broker_ipc.py` | Length-bounded Unix framing, total receive deadline, kernel peer credentials, content-free errors, disconnected-client survival. |
| `tools/autonomy_broker_operator.py` | P256 operator signature verification and persistent one-use control receipts. Replaying an old completed resume cannot resume again after STOP. |
| `tools/autonomy_guardian.py` | Existing Guardian, plus optional expected-generation validation **inside its control lock** for signed resume. This source change is not installed. |

The IPC module intentionally does not yet bind a production socket or grant
authority. Operator and worker sockets must be separate so long verification
cannot block STOP. Operator public-key enrollment is not a worker/relay action.
No mobile bearer token alone grants resume authority.

`CoreRelease` now requires a trusted data-safety admission callback before even
stopping the old container. There is no production implementation of that
callback yet. Tests inject fake admission only to exercise transition ordering.
Those tests are **not** evidence of safe production data rollback.

Container compensation is named `container_restored`, not `rolled_back`: it does
not prove database/schema restoration. Lost acknowledgements and failed
compensation remain reconciliation-required, not successful.

## Evidence limitations and unfinished installation boundary

The following must be completed before requesting the second sudo action:

1. Protected production job/worktree registration, local exact-tree validation,
   artifact receipts and a dispatcher connecting the existing validated modules.
2. Fixed sandbox execution/build wiring, restart cleanup and cancellation of
   daemon-owned containers; no caller image/argv/mount authority.
3. Release data/rollback admission and durable release records. Existing
   `tools/deploy_unified_core.sh` checks database health but is not a transactional
   data rollback implementation. Replacing an image alone does not undo startup
   migrations or data changes. Do not substitute `lambda _: True` for admission.
4. Exact canonical worktree cleanliness/source verification without executing
   caller-controlled Git hooks, filters, configuration helpers or source scripts
   as root.
5. Production operator adapter passing the signed generation into Guardian's
   locked resume check; root-enrolled public keys; fixed socket ownership and
   hardened separate units. An empty key enrollment must deny remote resume.
6. A root-reviewed, idempotent upgrade installer. It must validate the installed
   Guardian manifest, preserve STOP, stage root-owned modules, reject unknown or
   modified payloads, and leave normal Core/Developer untouched.
7. Complete disposable-filesystem rehearsal of that upgrade, including modified
   payload refusal and repeat-install behavior. Only then provide its exact
   one-line sudo command. No such command is ready at this checkpoint.

No autonomous Developer gateway deployment is implemented: the current installer
runs repository code as Aaron, whose host authority includes Docker and operator
resume. It must not be used as an autonomous release shortcut.

Only Python/security verification profiles exist. Android/Wear builds require a
separately provisioned pinned offline image/profile; unknown profiles are denied.
Current broker command results expose exit category, byte count and output hash,
not source/prompt/test output contents. Content-safe actionable diagnostics still
need an explicit contract if required by the Governor.

## Validation commands already run

```sh
/home/aaron/.venvs/jarvis-phase2/bin/python -m pytest -q tools/tests/test_autonomy_broker*.py tools/tests/test_autonomy_guardian.py
/home/aaron/.venvs/jarvis-phase2/bin/ruff check tools/autonomy_broker*.py tools/autonomy_guardian*.py tools/tests/test_autonomy_broker*.py tools/tests/test_autonomy_guardian.py
/home/aaron/.venvs/jarvis-phase2/bin/ruff format --check tools/autonomy_broker*.py tools/autonomy_guardian*.py tools/tests/test_autonomy_broker*.py tools/tests/test_autonomy_guardian.py
/home/aaron/.venvs/jarvis-phase2/bin/mypy --explicit-package-bases --follow-imports=silent --check-untyped-defs tools/autonomy_broker*.py
```

All passed. Compileall, shell syntax, focused Bandit at the repository's
high-severity gate, and `git diff --check` passed. Existing Guardian systemd units
verified; only unrelated host XFS `CPUAccounting` deprecation warnings appeared.
The focused Guardian tests include `visudo -cf` against a temporary policy, not
host sudo changes. No whole-repository suite was rerun.

GitHub provenance implementation references the official
[check-runs API](https://docs.github.com/en/rest/checks/runs),
[workflow-runs API](https://docs.github.com/en/rest/actions/workflow-runs), and
[pull-request reviews API](https://docs.github.com/en/rest/pulls/reviews).
The current provenance tests use deterministic fixtures, not a live autonomous
PR or release.
