# Android supervised-development product: deployment handoff

This is a source-release handoff, not deployment authorisation or evidence of a
successful live coding task. Guardian must remain STOPPED/trusted throughout
installation and read-only acceptance. No production request, provider call,
Android installation, signing operation or release publication was performed by
preparing this change.

## Release identity and approval boundaries

| Item | Reviewed value / prerequisite |
| --- | --- |
| Canonical branch | `jarvis/unified-production`; deploy only the approved merge SHA after exact-head CI and CodeQL |
| Installed broker source | `8902197c9a7f1c44c6251fe765c6cafcec31952721009158984fd593bcea87f3` |
| Replacement broker | `5663cf972a93fc0590a9b39e68cfb7ab219e6fe5375716ccd2ef2b0433edbc8b`; must match `UPGRADE_BUNDLE` and `fingerprint(payload())` |
| Destination manifest | 42 records, including preserved policy/private configuration and the new integration payload/units |
| Android candidate | `19.0.0-alpha49` / `190510`; Phone and Wear; not tagged, signed or published by this work |
| Preserved contracts | `com.aaron.jarvisvoice`, Core API `3.7.0`, realtime protocol `2`, existing storage and Android Keystore identities |

Source merge, protected migration, Core/Gateway deployment, integration identity
provisioning, normal OTA publication/installation, and the live trial require
their respective owner approvals. A task preparation signature is not a coding
approval; coding approval is not Guardian activation; none of those authorises
GitHub publication. Draft-PR publication has a fourth, proposal-bound signature.
Independent human review, protected CI/CodeQL and explicit merge/release approval
remain separate gates.

## Ordered deployment — do not execute from the feature worktree

1. After approved merge, fast-forward the clean canonical checkout to the
   approved remote SHA without discarding local changes. Preserve the previous
   broker manifests, source snapshot, Core/speaker image IDs, gateway release
   target and user unit. Record their identities without copying credentials
   into source, evidence reports or Git.
2. Verify the exact installed source manifest/file inventory, ownership, private
   configuration digest, Guardian trust/state/generation, enrolled operator
   keys, existing controller DB identity and audit continuity. All four broker
   sockets must already be enabled and active; the controller must be inactive
   or historically failed. Do not restart it to test readiness. A running
   controller or outstanding `upgrade-transaction.json` blocks migration.
3. Use the owner's existing independently reviewed immutable-source staging
   procedure for the **complete approved commit and imports**. Verify each
   tracked byte and Git executable bit, no extra files, root ownership and
   non-writable ancestors. Preserve the previous stage. The repository does
   **not** provide an immutable-source staging CLI. Internal installer staging
   does not make its already-imported source immutable. Therefore this is a
   command template, not a substitute for verified staging:

   ```sh
   sudo /usr/bin/python3 -I <verified-root-owned-stage>/tools/autonomy_broker_install.py --upgrade
   ```

   Enter sudo authentication only in the owner's terminal. Never run root Python
   from a mutable checkout. This exact-version migration rejects unknown,
   modified, partial or wrong-version installs. It stages/backs up executables
   before stopping all source broker roles, preserves policy/enrollment,
   databases/WAL, ledgers, jobs, audit and Guardian state, and re-enables only the
   four existing broker sockets. It does not start the controller or enable the
   new `jarvis-development-integration.socket`.
4. Reuse the **existing** sealed worker environment. Do not recreate it, run
   `--install-wheelhouse`, or substitute a development virtualenv. Its fixed
   lock-derived path is verified by the installed module:

   ```sh
   /usr/bin/python3 -I -B /usr/local/libexec/jarvis-autonomy-broker/tools/supervised_development_environment.py --verify
   systemctl show jarvis-autonomy-broker-development-controller.service -p ActiveState -p User -p Group -p SupplementaryGroups -p CapabilityBoundingSet -p ExecStart
   systemctl is-active jarvis-autonomy-broker-operator.socket jarvis-autonomy-broker-worker.socket jarvis-autonomy-broker-development-admission.socket jarvis-autonomy-broker-development-publication.socket
   ```

   The coordinator remains root with the existing restricted capability set and
   `SupplementaryGroups=jarvis-autonomy`; actual coding runs as the configured
   autonomy UID in its isolated namespace. Reverify manifest, Guardian and
   controller-data identity. Socket-activated services being inactive between
   requests is not a failure. Do not replace/initialise the existing controller
   DB or rewrite old candidate source bindings.
5. Refresh Core's trusted source snapshot to the approved canonical SHA through
   the existing tracked-only, credential-free snapshot procedure. Keep the old
   snapshot, read-only `/host-code/jarvis` mount and
   `JARVIS_DEVELOPMENT_SOURCE_ROOT=/host-code/jarvis`. Inspect effective live
   Compose configuration first; preserve `/app/data`, `/app/config`, `/app/logs`,
   speaker identity, networking and integration settings. Snapshot refresh is
   not implemented by the following deployment script and must not be assumed.

   Existing reviewed commands, run as the normal owner after deployment approval:

   ```sh
   cd /home/aaron/jarvis-unified-production-worktree
   bash tools/deploy_unified_core.sh --verify-only
   bash tools/deploy_unified_core.sh
   bash developer_gateway/install.sh
   curl --fail --silent --show-error http://127.0.0.1:8000/health
   curl --fail --silent --show-error http://127.0.0.1:8765/health
   ```

   Both scripts require a clean canonical branch matching fetched origin.
   Core uses `/home/aaron/.local/share/jarvis-runtime` by default and the existing
   Compose files there. **It builds and recreates both Core and speaker-verifier**;
   approval must cover that impact. It validates existing persistent mounts,
   read-only SQLite quick checks, health and source labels. Gateway installs a
   SHA-addressed release under `~/.local/share/jarvis-developer/releases`, retains
   older releases and existing credentials, changes its `current` symlink/user
   unit and restarts only `jarvis-developer.service`. Neither script is an
   automatic rollback manager.
6. Reverify authenticated Core/Gateway responses using existing credential
   handling; never print tokens or paste them into shell history. Core must
   validate the exact snapshot SHA. Gateway must return truthful idle, blocked,
   expired or unavailable state, never an empty approval form. Read-only GETs
   must not create tasks or start the approval countdown. Preserve historical
   candidates; a stale source binding is not silently rebound.

## Separate draft-PR integration provisioning

The new units are installed **disabled/unprovisioned**. No setup command exists
in this repository; do not invent one or run the integration as root/autonomy.
An owner-reviewed provisioning operation must establish:

- A dedicated `jarvis-integration` UID/GID, distinct from root, the autonomy
  worker and gateway owner. The unit grants `SupplementaryGroups=aaron` only
  inside its sandbox for fixed socket access, not account-global membership.
- Root-owned `/etc/jarvis-development-integration/policy.json`, regular single-
  link file with root-owned non-writable ancestors, no group/world writes and
  readable by the integration identity. Its exact schema is:

  ```json
  {
    "version": 1,
    "repository": "aaronhall105/jarvis-ai",
    "base_branch": "jarvis/unified-production",
    "integration_uid": 12345,
    "github_actor_id": 12345,
    "owner_public_keys": {"SHA256_OF_DER": "BASE64_DER_P256_PUBLIC_KEY"}
  }
  ```

  The numbers and public-key strings above are **schema illustrations, not
  provisioning values**. Resolve the actual dedicated UID/GitHub actor ID and
  copy only the already-enrolled owner's verified public key; key ID must be
  SHA-256 of its DER bytes. Never generate/replace the owner's key.
- A repository-restricted integration credential at the fixed systemd
  `LoadCredential` source `/etc/jarvis-development-integration/github-token`,
  protected from gateway/worker access. Restrict its GitHub authority to this
  repository's Contents read/write, Pull requests read/write and required
  Metadata read; no additional administration, Actions/workflow or deployment
  permissions. GitHub's Contents-write permission itself can cover release
  APIs: the token is not intrinsically release-incapable. The fixed integration
  adapter exposes no release route; the separate UID, sandbox and LoadCredential
  boundary must protect that credential from the worker and generated code.
  Verify the authenticated GitHub actor matches policy. The service
  requires its loaded credential to be a single-link file owned by its UID,
  mode `0400`; never expose its value.

Only after those prerequisites and separate activation approval:

```sh
sudo systemctl enable --now jarvis-development-integration.socket
```

This enables the fixed socket, not a PR or coding task. The service runs with no
capabilities, `ProtectSystem=strict`, `ProtectHome=true`, no broker/Guardian data
or Docker access, and writes only its private
`/var/lib/jarvis-development-integration` journal. It fetches verified proposals
from the root publisher over the fixed Unix socket, checks real peer UIDs,
revalidates the exact independent-review receipt/diff and requires the separate
`jarvis-development-integration-v1` owner signature. GitHub writes are limited
to blobs, trees, commits, the task branch and one draft PR; no merge/deploy route
exists. Reconnect reads the real journal and never retries a dispatch.

## Android release and first live acceptance

Use only the existing `.github/workflows/android-ota-release.yml` after separate
reviewed canonical/tag/release approval. Alpha49 has not been tagged, signed or
published by this change. Verify immutable release provenance, Phone/Wear
`190510`, package identity and the existing production signer:
`009fd523f27cf94eb98917e17670804897e6378e5eccf1ce3ead680721691aac`.
Use the normal OTA in-place update, never uninstall/clear data. Verify the
installed phone version and enrolled Android Keystore public key separately;
APK certificate equality does not establish operator-key enrollment.

The implemented owner flow is:

1. Ordinary authenticated chat/voice routes the genuine owner enhancement via
   `request_development`; or the native authenticated API registers a durable
   UUID at `POST /api/improvement/development`. Reconnect uses its GET resource.
   `needs_review` means source inspection/proposal is still required, not a
   diagnosed defect. `inspect_development_source` / `propose_development_scope`
   verify real tracked anchors/hashes and record scope without rewriting the
   original evidence. The existing legacy loops cannot claim these requests.
2. Samsung reviews the exact protected preparation preview at
   `GET /api/autonomy/development/prepare/{candidate_id}` and explicitly signs
   `prepare_development`; `POST /api/autonomy/development/start` relays it. This
   starts the controller, which performs no-network environment, credential
   presence, pricing, source/prompt and budget checks **before creating a task
   or starting the 90-second approval window**. Failed checks create no task and
   publish `development_execution_preflight_failed`, not an empty form. Success
   records a root-owned, non-secret readiness receipt bound to that task,
   request, source, parameters and expiry. Activation requires that receipt;
   worker launch repeats the verification. A signable preparation preview is
   not proof that provider credentials are available, that a model is remotely
   available, or that prices are independently current. Verify those deployment
   prerequisites and physical-device readiness before beginning preparation.
3. Samsung separately signs the published `approve_development` through the
   existing `/api/autonomy/control` route, then reviews/signs the task-bound
   activation preview and `/api/autonomy/development/activate`. Generic HTTP
   RESUME remains prohibited. The controller cannot approve or activate itself.
4. The bounded worker must leave an independently verified diff at
   `REVIEW_REQUIRED`, with actual provider-call counts, recorded usage-based cost
   estimates, real check evidence and authoritative
   Guardian STOP confirmation. Cancellation/timeout/uncertainty must not replay
   a provider request. No live success is asserted until this occurs on the host.
5. Samsung reviews `/api/autonomy/development/proposal/{task_id}` and, only if
   desired, separately signs `/api/autonomy/development/publish`. Unprovisioned
   integration remains blocked. A `PR_REVIEW_REQUIRED` receipt is not authority
   to merge, deploy or publish OTA.

## Rollback and uncertain effects

Protected migration retains durable old executable/manifest backups and
`upgrade-transaction.json`. A handled failure first quiesces affected services,
restores only recorded payload/manifests and re-enables the original four
sockets; it does not restore databases, erase ledgers or start the controller.
Uncertain quiescence/restoration or interruption preserves transaction/backups
and rejects another upgrade with reconciliation-required. There is no generic
`--rollback` or `--reconcile` installer flag. Do not delete the marker or repeat
installation to repair it; inspect the exact retained transaction and obtain
approval for that recorded recovery.

Before Core/Gateway rollout, record old Core/speaker image IDs, effective Compose
files/source-mount target, gateway `current` target and complete user unit. A
separately approved rollback restores those exact images/source mount or old
gateway symlink+unit, reloads/restarts only affected services, then repeats health,
SHA, mount and read-only DB checks. Preserve new and old releases and every
persistent database; neither rollout script supplies automatic rollback.

An uncertain GitHub write is durably `DISPATCHING` or
`RECONCILIATION_REQUIRED`; preserve its task/request/proposal identifiers and
inspect the actual remote branch/PR before any approved recovery. Do not resend
the signature, delete journals or create a second PR. The integration has no
automatic reconciliation command.

## Verification evidence and remaining live gates

Read-only live inventory during this work found Core and Gateway healthy at
`8140346f5e5c05c34445b0c7ea3137f39c7f35ee`; the production checkout was clean at
that SHA. The installed broker marker reports the 37-record `8902197c…` bundle.
Guardian was trusted and STOPPED, generation
`02c6a986-e1fd-40d2-bab5-67c20ab41278`, and the controller was inactive.
These components have not received this new product interface. ADB reported no
connected device; the previously reported Alpha48 phone installation was not
physically reverified. No pairing was initiated.

All five disposable process rehearsals passed on the final protected source.
They use the real controller entrypoint, Unix peer credentials (gateway UID
1000, worker UID 600 and integration UID 700), real signed test identities and
Guardian generation transitions. Success performs two loopback SDK requests,
including a failed test and bounded repair, real isolated verification,
REVIEW_REQUIRED, confirmed STOP and a separately signed draft-PR handoff to a
fake GitHub transport. The host service manager is represented by a fixed
disposable service-manager fixture; this is not live systemd or phone acceptance.
Disconnect, denied sandbox, termination and STOP cases demonstrate no uncertain
request replay.

Phone 351 and Wear 1 unit tests and both lint gates passed. Java and Android JSON
tests accept the exact process-produced proposal, diff, verification receipts
and two-call usage evidence. Gateway: 282 tests passed. Dependency audit found
no known vulnerabilities; pinned Actionlint and source-only Gitleaks passed.
The raw working-tree scan also encountered generated bytecode with test/redactor
private-key markers and a non-secret Python curve-expression false positive;
no credential was found. No suppression was added: generated caches are not
release source, and the misleading local variable was renamed.

Unsigned release APK hashes (not production release assets):

- Phone: `52d052c8116206f0cd4c1b355cd126bc5d388c9ade8ae22d2095df66a679564f`
- Wear: `7e16332141ffba6127dd4f33af0bd35655521b0b80ed3cac7a98117ce82384ad`

Personally verified during implementation: 25 dedicated Core development-request
tests; 17 real Unix-peer/framing/integration-service tests; Core mypy (90 files),
Ruff/formatting and high-severity Bandit checks passed. A preceding full Core run
passed 1,730 tests plus 143 subtests; three pre-existing opt-in real deployment/
rollback rehearsals were skipped because deployment was not authorised. Those
counts are local evidence, not final exact-head GitHub status; the PR must attach
its final combined regression, Android, security, process-rehearsal, CI and
CodeQL results before merge.

Live migration, compatible Core/Gateway rollout, integration provisioning,
physical Samsung signing and a real authorised coding/PR cycle are still pending.
The historical live HTTP 403 cause remains unconfirmed without its correlated
audit evidence; deterministic signature tests do not reconstruct that event.
No claim of operational autonomous development follows from these source tests.
