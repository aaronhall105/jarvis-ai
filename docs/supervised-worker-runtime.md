# Reviewed supervised-worker runtime migration

This follow-up starts from canonical source
`8140346f5e5c05c34445b0c7ea3137f39c7f35ee`. It does not authorise installation,
service activation, an approval request, Guardian activation or provider use.

## Verified source and installation boundaries

- Installed bundle, read-only verified during this change:
  `73e84548db287fb02daa86d9da8634ae62fec39f615322979e9e74dfd1f93489`.
  Its exact 35-record inventory includes 33 reviewed payload files, policy and
  private Docker configuration. All 34 readable records passed hashes and
  root-owned, non-writable metadata checks. The private configuration was not
  readable by the development account; owner preflight must verify it.
- The installer reconstructs the exact source inventory independently of the
  destination and recomputes its fingerprint from file contents and modes.
  An unknown bundle, altered manifest, missing/extra file, modified payload,
  writable/symlink payload or interrupted transaction is rejected.
- The destination adds `supervised_development_environment.py` and its exact
  wheel/Python lock to the protected manifest: 37 installed records. The final
  reviewed `UPGRADE_BUNDLE` in `tools/autonomy_broker_install.py` is
  `8902197c9a7f1c44c6251fe765c6cafcec31952721009158984fd593bcea87f3`.
- Root broker, publisher and coordinator continue to execute
  `/usr/bin/python3 -I`. They do not import worker site-packages. Only the
  explicitly delegated, unprivileged worker uses the separately sealed
  `/opt/jarvis-supervised-worker-473c353b00a7f96b3786` environment. Its lock digest
  is `473c353b00a7f96b37862e5e8346e0d5b84299e21be88f61321b5661b3521a39`.
- Worker and pinned-pip bootstrap use `-I -B -X pycache_prefix=...` with the
  fixed environment's empty, sealed `bytecode-disabled` directory. They never
  load unpinned generated OS bytecode caches. Base source/native files and
  sourceless bytecode are pinned; generated OS cache ownership/modes are checked
  without making incidental cache creation alter the source identity. The
  pinned pip wheel bootstraps installation, not an unpinned `ensurepip` wheel.
- The verification child has no credentials, network or host process view.
  Its empty read-only `/proc` works under the existing systemd proc masks;
  `/usr/bin`, `/usr/lib` and `/usr/lib64` are individually read-only, rather
  than exposing all of `/usr`. No capability, DAC, namespace or unit sandbox
  restriction is relaxed.

## Ordered deployment, requiring separate owner authorisation

### Public/private manifest correction required before migration

The installer merged in `dfb38228dc27f002e07c1e9976ebc4e39b2c9b26` copied
all destination hashes into the public Guardian manifest, including the private
Docker client configuration. **Do not execute that staged installer.** The
corrected installer excludes only the exact, verified private configuration
entry, including when an earlier manifest already exposed it. The private file
remains hashed by the root-verified broker manifest; Guardian continues to pin
that complete broker manifest and every public payload. An unexpected private
hash is rejected rather than silently removed.

This correction changes installer source, not installed payload bytes. The
source bundle remains `73e84548db287fb02daa86d9da8634ae62fec39f615322979e9e74dfd1f93489`
and destination remains `8902197c9a7f1c44c6251fe765c6cafcec31952721009158984fd593bcea87f3`.
The corrected source commit/tree/archive identities must nevertheless be
approved and staged anew after merge; the old archive checksum is not approval
of the corrected installer. Verify every extracted file against its Git object,
including Git executable bits, root ownership, non-writable ancestors and the
absence of untracked or additional executable content. A matching archive hash
and file count alone do not verify the extracted installation source. Preserve
the previous stage; never execute root Python from the development checkout.

Regression coverage includes already-repaired and previously-exposed public
manifests, actual root and UID 995 readers in a disposable container, private
read denial, private/manifest tamper rejection, exact rollback after manifest
failure/interruption, and unchanged Guardian state, generation and audit bytes.
These are isolated tests, not evidence of a completed live migration.

1. Require exact-head protected CI/CodeQL and owner merge approval. Record the
   resulting canonical SHA. Retain the current source, broker payload, manifests
   and previous worker environment. Do not run root Python from a writable
   checkout. Use the existing independently verified, root-owned immutable
   source-staging procedure for the complete approved source and imports.
   The repository still does not provide an immutable source-staging CLI.
2. Owner preflight verifies all 35 installed records, Guardian STOPPED/trusted
   and unchanged generation, operator enrollment, audit continuity, private
   configuration, controller DB identity and no pending upgrade transaction.
   The installer now **enforces** all four source sockets loaded, enabled and
   active, with the controller inactive or historically failed. It refuses to
   terminate a running controller merely to satisfy upgrade prerequisites.
3. Run the reviewed immutable staged installer with local owner sudo:
   `/usr/bin/python3 -I <verified-stage>/tools/autonomy_broker_install.py --upgrade`.
   The stage must resolve to the approved commit, not a mutable worktree. This
   operation never installs Python dependencies or starts the controller.
4. Prepare the wheelhouse as the normal owner using a Python 3.14 interpreter
   with pip, **not** a root shell. The existing isolated development environment
   can download wheels but is never adopted as the production worker runtime:

   ```sh
   /home/aaron/.venvs/jarvis-phase2/bin/python -I -B /home/aaron/jarvis-unified-production-worktree/tools/supervised_development_environment.py --download-wheelhouse /home/aaron/jarvis-worker-wheels-473c353b00a7f96b3786
   ```

   This requires the clean canonical checkout to contain the merged lock. Only
   exact hash-locked binary wheels are downloaded; a pre-existing destination
   is not reused or overwritten. No provider credential is required.
5. Under separately approved worker-environment provisioning, run:

   ```sh
   sudo /usr/bin/python3 -I -B /usr/local/libexec/jarvis-autonomy-broker/tools/supervised_development_environment.py --install-wheelhouse /home/aaron/jarvis-worker-wheels-473c353b00a7f96b3786
   ```

   This operation is offline. It verifies the root-owned module/lock, exact
   system Python/stdlib hashes and the complete wheel inventory, copies and
   hashes wheels into private root-owned staging before package installation,
   then seals the new versioned environment and records all file hashes. It
   never writes the system interpreter or global packages, never replaces an
   existing environment, and never starts a task. Enter sudo authentication in
   the owner's normal terminal, never a Codex background prompt.
6. Read-only environment acceptance:

   ```sh
   /usr/bin/python3 -I -B /usr/local/libexec/jarvis-autonomy-broker/tools/supervised_development_environment.py --verify
   systemctl show jarvis-autonomy-broker-development-controller.service -p ActiveState -p User -p Group -p SupplementaryGroups -p ExecStart -p CapabilityBoundingSet
   systemctl is-active jarvis-autonomy-broker-worker.socket jarvis-autonomy-broker-operator.socket jarvis-autonomy-broker-development-admission.socket jarvis-autonomy-broker-development-publication.socket
   ```

   Reverify the complete installed manifest and Guardian state, plus the
   controller DB inode/content and authenticated gateway's no-task response.
   A historically failed inactive controller is not an instruction to restart
   it. Rehearse the actual namespace restrictions without `run_once`, an owner
   approval, provider credentials or production task creation.

The model, secure provider credential, verified current pricing, an exact
current-source candidate, Android approval and separate Guardian activation
remain independent requirements. This provisioning does not grant any of them.

## Rollback and interrupted-upgrade reconciliation

The migration stages all executable/configuration replacements and retains
durable old payload and Guardian-manifest backups before stopping broker roles.
A root-only `upgrade-transaction.json` records exact source/target identities,
backup paths, staged paths and source digests. The controller database, WAL,
ledgers, jobs, audit history and private configuration are never restored from
copies or replaced. Policy bytes, including enrollment, are retained exactly.

On a handled failure, all affected roles must first be confirmed stopped. Only
then are previous executable/configuration bytes and manifests restored. The
original four sockets are re-enabled; individual socket-activated services are
not forced to their former active state, and the controller is not started.
Successful rollback removes only this transaction's staging and backup files.

If service quiescence or restoration is uncertain, no executable restoration or
automatic restart is attempted past that boundary. Backups and the transaction
record remain. A subsequent `--upgrade` refuses with
`upgrade_reconciliation_required`. A process kill or power loss likewise leaves
the record for owner reconciliation. Do not retry, delete the marker, rewrite
manifests or restore databases. An owner must inspect the retained source/target
digests and service state and approve recovery of only the recorded payloads;
there is deliberately no generic automatic repair command.

Worker provisioning is separate: failed provisioning preserves its incomplete
new versioned directory, which cannot pass environment verification. It does
not remove or replace the prior environment. Do not manually chmod an incomplete
environment into acceptance or redirect the privileged broker interpreter.

## Regression evidence

The installer tests reconstruct every old payload byte from the immutable
baseline, including the dependency-free installed scope policy. Disposable
fixtures cover successful direct migration, unchanged initialized SQLite data,
raw policy/enrollment preservation, missing/extra/modified/unknown source,
self-consistent forged manifest, hostile workspace inode replacement, socket
and controller preconditions, manifest failure, interruption, activation failure,
failed rollback quiescence, retained backups and refusal to replay an uncertain
transaction. No live upgrade is used as a test.

Environment validation additionally exercises a real disposable root-owned
offline installation, complete inventory verification, tampering rejection and
imports as UID 600: 20 focused tests plus one Docker rehearsal passed. The 47
exact locked package versions passed the vulnerability audit with zero skipped
packages and zero known reported PyPI vulnerabilities. This is
not evidence that the environment has been installed on the Ubuntu host.

Final source validation used a disposable, non-hardlinked checkout with the
complete reviewed source overlay, normal source permissions and
`TMPDIR=/var/tmp`; production and development worktree permissions were not
changed. The Python suite completed with **2,895 passed, 143 subtests passed,
11 skipped, zero failures and zero errors** in 247.995 seconds. The skips are
opt-in/environment-dependent checks, not suppressed failing assertions.
The separately enabled process rehearsal passed all six tests, including real
controller lifecycle outcomes and the masked-`/proc` verification boundary.

Phone, Wear and shared-protocol unit tests passed **357 tests, zero failures,
zero skips**. Ruff checks and formatting, active/targeted Mypy, the Bandit
high-severity gate, systemd unit verification and the pinned Gitleaks scan of
the exact source export passed. These results certify disposable source
validation only; provisioning and a separately authorised live trial remain
outstanding.
