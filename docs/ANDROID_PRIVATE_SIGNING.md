# Private Alpha43 signing infrastructure — reviewed proposal, not activation

Baseline: `44d385ba4c0ec14bfb8dd81ff1825a21d46615c2` (PR105).
Phone/Wear remain `com.aaron.jarvisvoice`, `19.0.0-alpha43`, `190450`.
Production certificate SHA256 remains
`009fd523f27cf94eb98917e17670804897e6378e5eccf1ce3ead680721691aac`.
No Android, Core, gateway, storage, signing-key, public OTA or Guardian changes.
Guardian stays STOPPED. Observer stays disabled by default. No Phase2 permission.

## Audit and reuse of PR99

Reviewed proposal head: `a586125786fa7971138a9cbd1bb72981f059eb5d`.
It was based on alpha40, not current canonical. Its branch and PR98 are preserved.

Reused independently reviewed pieces:

- `.github/workflows/android-artifact-only.yml`: manual dispatch, commit-pinned
  actions, canonical-only source, unsigned build and separate approval/signing job.
- `tools/artifact_signing.py`: read-only GitHub metadata, exact PR/squash/tree,
  protected CI and separate PR CodeQL gates, bounded fixed-file payload, APK
  identity/certificate verification, SDK-only signing and temporary key cleanup.
- `tools/tests/test_artifact_signing.py`: all 71 existing boundary regressions.
- Additive CI typing/Bandit checks for the signing module; existing gates unchanged.

Replaced or strengthened:

- **PR99's plaintext Actions artifact was not private.** This repository is public;
  authenticated users with repository read access can download Actions artifacts.
  Signed Phone/Wear and their provenance manifest now exist only in a private
  temporary directory on the signing runner. Only an authenticated encrypted
  bundle and public ciphertext receipt are uploaded. No plaintext signed APK is
  uploaded, including on failure. Unsigned build artifacts are not confidential.
- Operator ID must be Aaron's `291914934`. Each dispatch includes an approved
  recipient certificate SHA256 visible in the signing job name. Required reviewer
  is Aaron only; approval history for that run/environment must explicitly agree.
- Run attempts other than 1 fail closed: run-wide approvals cannot authorize a
  replay. Use a new manual dispatch and fresh approval after any failure.
- Unknown additional required check contexts fail closed rather than being ignored.
- Old alpha41/42 recovery sequencing and stale physical evidence were NOT copied.
  No old certification claims or unrelated observer documentation were imported.

## Threat boundary and workflow

The workflow accepts only exact source SHA, merged PR number and encryption
certificate fingerprint. It has read-only contents/actions/checks/pull-request/
security-events permissions. No PAT, OIDC or write grant; no release/tag/feed API,
device access, host service command, publication mode or installation step.
Existing tag-based public release workflow is byte-unchanged.

1. Dispatch from canonical. Source SHA must equal GitHub dispatch SHA, workflow
   SHA and live canonical HEAD. The supplied merged PR's squash must equal it.
   Correct repository identity, protected checks from GitHub Actions on PR and
   canonical, zero-finding matching-tree PR CodeQL analysis and no PR alerts are
   required. Existing unrelated canonical CodeQL alerts are not dismissed.
2. Environment configuration is checked BEFORE building. Missing/unreadable
   environment, wrong reviewer/policy or stale checks deny. Build has no signing
   secret inputs. Run Phone/Wear/protocol tests, release lint and unsigned builds.
3. The unsigned pair and manifest cross jobs by same-run immutable artifact ID;
   SHA256 is bound through job outputs, not trusted from the downloaded manifest.
4. Fresh signing runner waits for native `jarvis-artifact-signing` approval. It
   verifies the explicit Aaron approval record, canonical HEAD/checks again,
   payload/hash/identity and recipient certificate. No Gradle/candidate build
   executes with signing secrets. Only fixed SDK commands receive passwords.
5. Use the existing production key on the runner, verify each APK with aapt and
   apksigner, and produce exact signed hashes/provenance. Keystore file is 0600
   outside upload paths, removed on success/failure. Errors never echo tool stderr.
6. Encrypt the exact three-file signed bundle using OpenSSL CMS AES-256-GCM and
   RSA-OAEP with SHA256/MGF1-SHA256 for ONE operator recipient (RSA 3072–8192 bits,
   valid for at least another day). Bind the certificate DER SHA256 to approval.
   The recipient is NOT the Android production certificate. No decryption key is
   accepted by the workflow. Delete plaintext with temporary-directory cleanup.
7. Recheck approval/source/checks before success. Upload only `phone-wear.cms` and
   `receipt.json`, immutable run-specific name, 30-day retention. Receipt records
   ciphertext SHA256 and source/check/run/recipient evidence. The encrypted manifest
   contains APK hashes/sizes/signing certificate and expressly denies publication
   and installation. Job failure cannot execute the default-success upload step.

Ciphertext is downloadable, but only the operator's separately held private
encryption key can recover signed APKs. This is encrypted retention, not a claim
that GitHub provides a private-artifact ACL on a public repository. Public source,
unsigned APKs, run metadata and ciphertext receipt remain visible.

The existing signing secrets are repository-scoped. This proposal protects THIS
reviewed workflow; it does not claim that an arbitrary authorized repo writer
could never propose a different workflow using those secrets. No secrets are
moved or rotated here. Autonomous workflow authority remains disabled. Protected
review of workflow changes and human-only signing authorization remain essential.

## Approval environment: possible, absent, NOT configured by this work

Read-only API audit on 2026-10-29: public repository, authenticated Aaron ID
291914934 with admin permission, zero environments. GitHub supports required
reviewers on public repositories. Actual configuration/approval execution remains
unverified until the operator performs the separately authorized setup.

After review/approval, Aaron uses repository Settings → Environments → New
environment, exact name `jarvis-artifact-signing`:

1. Required reviewers: ONLY `aaronhall105` (ID 291914934). Save.
2. Deployment branches/tags: **Protected branches only**. The workflow additionally
   restricts itself to `jarvis/unified-production`; tags/features are rejected.
3. Disable **Allow administrators to bypass configured protection rules**. An
   administrative start without explicit Aaron approval also fails the script.
4. Single-operator model: leave Prevent self-review off deliberately, allowing
   Aaron to dispatch then separately approve. If independent approval is required,
   stop and review the policy instead; do not silently add reviewers or bypass it.
5. Add environment VARIABLE `JARVIS_ARTIFACT_RECIPIENT_CERT_PEM` containing only the
   public encryption certificate described below. Never upload its private key.
6. Keep all existing Android signing secrets unchanged. Do not copy them into
   this repository/worktree or change secret scopes as an incidental setup step.

Missing configuration is a deliberate NO-GO. Referencing an environment must not
be used to auto-create an unprotected environment: the build preflight refuses
before the dependent signing job is eligible. `deployment: false` suppresses
GitHub deployment records without removing environment protection rules.

### Separate encryption identity — operator setup, not executed here

Aaron should generate/back up an encrypted RSA-3072 encryption private key locally
outside all repositories and the autonomy identity's access. This is NOT a new
Android signing key, Android Keystore key or signing migration. Example commands
to run only after setup approval, in a new private directory with `umask 077`:

```sh
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -aes-256-cbc -out artifact-recipient.key.pem
openssl req -new -x509 -key artifact-recipient.key.pem -sha256 -days 365 -subj /CN=Jarvis-private-artifacts -out artifact-recipient.cert.pem
openssl x509 -in artifact-recipient.cert.pem -noout -fingerprint -sha256
```

Enter any encryption passphrase only into the local OpenSSL terminal. Do not send
it here. Only certificate PEM and lowercase colon-free DER SHA256 go to GitHub.
Confirm a local encrypt/decrypt round trip and secure backup before approval.
Lost private key means retained ciphertext cannot be used for recovery.

## Canonical-only Alpha43 and recovery sequence

Merging this infrastructure changes canonical SHA but not Alpha43 application
source/identity. Therefore **do not dispatch old `44d385b...` or PR105** afterward.

1. Approve this infrastructure PR's merge separately. Capture actual new squash
   SHA S, require all four post-merge checks plus matching PR security evidence.
2. Verify Alpha43/190450 and same application/storage contracts at S. Confirm
   environment/encryption setup. Separately authorize manual signing dispatch
   against canonical with `source_sha=S`, `reviewed_pr=<this infrastructure PR>`,
   and the operator's encryption fingerprint. Aaron explicitly approves the job.
3. **Keep canonical at S until that run finishes and both signed APKs are recovered
   and independently verified.** Then securely retain Alpha43 outside expiring
   Actions storage. No tag/public release/OTA promotion is needed or permitted.
4. Prepare a separately reviewed known-good Android recovery source on current
   canonical, preserving this signing infrastructure and all newer gateway/security
   changes. Choose an unused version/code strictly greater than 190450 AND all
   then-published/reserved versions. Alpha44/190460 is only an example, not reserved
   here. PR98's alpha41/190430 cannot recover an installed Alpha43; do not merge it
   blindly or overwrite current canonical with its old tree.
5. Obtain explicit recovery source/sequence approval, merge its separately green
   PR, record new canonical squash R, and wait for R's exact checks/security gate.
   Authorize a second private dispatch with `source_sha=R`, that recovery PR and
   the same recipient. Keep canonical R until verification finishes. Do not try
   to sign old S after canonical moves; that intentionally fails.
6. Hold BOTH verified signed Alpha43 and higher-code recovery Phone/Wear artifacts
   before requesting physical installation. A canonical recovery merge alone must
   NOT deploy Core/gateway or update any device/feed. Before normal development
   resumes, use a separate reviewed feature-return PR preserving the working
   feature tree with a still-higher code; its merge/signing need separate approval.

Source changes/CI reruns during any signing phase deny the run rather than silently
switching source. Coordinate canonical merges; use a fresh dispatch for a new SHA.
Never force-push, retarget tags, sign an unmerged feature or restore stale databases.

### Independent retrieval and verification — not performed yet

Download the exact run/attempt artifact ID while retained; verify GitHub provenance
and receipt ciphertext SHA256. Decrypt locally with the separate encryption key:

```sh
openssl cms -decrypt -binary -inform DER -in phone-wear.cms -recip artifact-recipient.cert.pem -inkey artifact-recipient.key.pem -out verified-bundle.tar
```

Require exit zero BEFORE examining/extracting output: failed authenticated
decryption may leave partial unauthenticated bytes. Extract only expected regular
files from a verified archive in a new private directory. Independently check
Phone/Wear with SDK36 `apksigner verify --verbose --print-certs` and `aapt dump
badging`; compare package, production certificate, version, non-debuggable flag,
SHA256, source/PR/tree/run/recipient and encrypted manifest. Compare installed
Samsung signer/version with read-only evidence. Store encrypted bundle, receipt
and verification results securely before Actions retention expires.

No installation is implied. Later approved updates must be in-place and same-key;
never uninstall, clear app data, downgrade, replace keys or restore old app data.
Retained conversations/settings/auth and operator Keystore identity still require
physical before/after acceptance. Full autonomous rollback remains Phase5, not
silently certified by this maintenance signing infrastructure.

## Evidence / limitations

Existing PR99 harness: 71 passed unchanged before integration. New regressions
cover operator identity, rerun denial, encrypted-only upload, real disposable
encryption/decryption, tamper denial, wrong/missing recipient, plaintext cleanup
and Alpha43/higher-version policy. No production signing credential was accessed.
Exact local totals and protected CI/CodeQL are recorded in the new PR evidence.

Local validation completed 2026-10-29 (Python 3.14.4; authoritative Python 3.12
validation is the exact-head GitHub CI, not inferred from local results):

| Check | Local result |
| --- | --- |
| Full bridge/tools/gateway pytest | 2,326 passed, 3 gated deployment tests skipped, 143 subtests |
| Signing/privacy regressions | 92 passed, including real disposable encryption, wrong-key and tamper rejection |
| Phone / shared protocol / Wear unit tests | 286 / 30 / 1 passed, zero failures/skips |
| Phone/Wear debug + release lint, debug + unsigned release builds | PASS, 195 Gradle tasks |
| Ruff / format / mypy / compileall / baseline | PASS; 267 formatted files, 117 existing + 1 signer typed source |
| Bandit / dependency audit | PASS; no known dependency vulnerabilities |
| Pinned CI actionlint / Gitleaks current source | PASS; zero findings, 9.01 MB scanned |
| APK SDK verification | Alpha43/190450, correct package, non-debuggable, unsigned |

The fresh worktree initially inherited umask 0002; five existing Guardian harness
tests correctly rejected group-writable installer source. Removing group-write
permission from those seven LOCAL source files (no source/Git mode or installed
Guardian change) resolved it; the complete suite then passed. One existing
Starlette/httpx deprecation warning remains. No safety check was weakened.

Unsigned Phone SHA256:
`f0fcae2eebeff057241a399b1424bf86f77cbf630fa8259eed37f93549e88fd7`.
Unsigned Wear SHA256:
`2f80d77aaf21fbb88ad6fa4133f7c7caa6a8288c5f867d9bc4d89981f03805cb`.
Both exactly match prior Alpha43 preparation; this infrastructure does not change
application bytes. These are NOT production-signed or installation-ready APKs.

Runtime environment approval, production signing, independent signed artifacts,
private-key custody and physical data preservation remain NOT TESTED. No signing
dispatch is authorized by implementation or a green PR. Guardian stays STOPPED;
Phase1 dashboard/observer/operator enrollment/STOP acceptance remains separate.

## References

- [GitHub artifact download permissions](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts)
- [Environment protection and plan support](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
- [Environment setup](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)
- [Environment metadata read permissions](https://docs.github.com/en/rest/deployments/environments#get-an-environment)
- [OpenSSL CMS authenticated encryption](https://docs.openssl.org/3.2/man1/openssl-cms/)
