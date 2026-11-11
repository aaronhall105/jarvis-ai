# Alpha47 candidate validation

Base: `19d4158b627bc781a07bc879aa2095126b681834`.
Validation date: 2026-11-11. This records candidate checks, not live publication.

## Read-only release reservation checks

- Canonical remote and clean source checkout matched the required base.
- No Alpha47 tag, release, open candidate PR or 190490 source reservation existed
  before this branch was created.
- Existing alpha feed: `19.0.0-alpha46`, 190480, source
  `58cb3afdfe2ed56caa8ed451344efb7ffe005090`.
- Feed release ID `379188230`, manifest asset ID `728176449`,
  manifest SHA-256 `c564fe8631c17ad1b01c477c72cca2af5715c69bc450ae516dce83333d63d0cc`.
  The existing feed reports `immutable:false`, `draft:false`.
- Repository immutability reports `enabled:false`, `enforced_by_owner:false`.
  Signing-secret names exist; values were not read. The new Administration-read
  settings secret is absent. Publication is blocked until separately configured.
- Historical release artifacts, package identities, storage/Keystore code,
  manifests, realtime protocol and protected broker source were not changed.

## Local checks

| Check | Result |
|---|---|
| Combined Core/tools/Developer Gateway suite | 2630 passed; 143 subtests passed; 3 existing opt-in real Docker deployment tests skipped |
| Final publication + release-baseline/historical preservation suite | 71 passed, including 55 publication tests (three extra multi-channel cases added after combined-suite collection) |
| Phone unit tests | 315 passed |
| Wear OS unit tests | 1 passed |
| Shared protocol tests | 30 passed |
| Phone/Wear release lint | Passed, zero errors; 194/24 existing warnings respectively |
| Phone/Wear release builds | Both unsigned builds passed; package `com.aaron.jarvisvoice`, 190490 / alpha47 verified with aapt |
| Ruff check / format | Passed across active Python source/tests |
| Mypy | 124 active files and separate two-file namespace gate passed |
| Bandit high severity | Passed for Core, Gateway and publication/signing tooling |
| Dependency audit | No known vulnerabilities |
| Pinned Gitleaks current source | No leaks |
| Pinned actionlint | Passed |
| Product baseline / compilation / git diff validation | Passed |
| Home Assistant contract checks | 12 tests and release-integrity check passed; no live integration calls |

The first combined run had one environmental failure: this host already has
`/tmp/.git`, so a disposable observer journal under `/tmp` correctly failed the
source-worktree exclusion guard. The complete rerun above used a clean `TMPDIR`
under `/var/tmp`; neither the guard nor the assertion was modified. A FastAPI
test-client deprecation warning remains. The three Docker skips are pre-existing
deployment/rollback opt-ins, not suppressed release tests.

Commands used (existing isolated Python environment):

```bash
TMPDIR=<clean-disposable-directory> PYTHONPATH=bridge python -m pytest -q \
  bridge/tests tools/tests developer_gateway/tests --basetemp=<disposable-pytest-directory>
python -m pytest -q tools/tests/test_publish_android_ota.py \
  tools/tests/test_verify_product_baseline.py tools/tests/test_android_alpha43_candidate.py \
  tools/tests/test_android_alpha44_recovery.py
python -m ruff check bridge/app bridge/tests developer_gateway tools
python -m ruff format --check bridge/app bridge/tests developer_gateway tools
python tools/verify_product_baseline.py --skip-ref
python -m mypy bridge/app developer_gateway tools/generate_ota_manifest.py \
  tools/generate_product_release_manifest.py tools/verify_product_baseline.py
python -m mypy --explicit-package-bases tools/artifact_signing.py tools/publish_android_ota.py
python -m bandit -q -r bridge/app developer_gateway -lll
python -m bandit -q -lll tools/artifact_signing.py tools/publish_android_ota.py
python -m pip_audit -r bridge/requirements.txt -r requirements-improver.txt \
  -r developer_gateway/requirements.txt --progress-spinner off
```

Android: `:wearprotocol:test :app:testDebugUnitTest :wear:testDebugUnitTest
:app:lintRelease :wear:lintRelease :app:assembleRelease :wear:assembleRelease`,
with all four signing environment variables explicitly unset. No production
keystore was accessed. Local unsigned APK hashes (not OTA artifacts):

- Phone: `00515fb58e36a1db9179dadcf44a9de94b2e2de5a61a7dd7db6cf1609b03d4a0`
- Wear: `99f7822c78a6386a981b1fbf6caa15a2ecd5874bc3aa4cc7f262f774603bf1ac`

Pinned scanners match existing CI. Gitleaks scans a disposable export of tracked
and candidate source, excluding ignored build/cache files; no scanner policies
or suppressions changed. Local raw logs are retained at
`/var/tmp/jarvis-alpha47-evidence/`. Exact-head GitHub CI and CodeQL results are
recorded on the PR; do not infer remote success from this local report.

Publication regression fixtures exercise duplicate/draft release rejection,
repository-setting/authentication failures, fresh/moved tag fencing, source and
signer binding, missing/tampered asset evidence, downloaded-byte/digest equality,
immutability verification, strictly monotonic mutable pointers, alpha/beta/stable
isolation, partial promotion, and uncertain-effect no-retry behaviour. Fixtures
are not signed APKs and do not contact or alter GitHub releases.

See [publication approval and recovery](ALPHA47_PUBLICATION.md) for the separate
operator actions. No tag, published asset, feed, device, Guardian state or live
service was changed during candidate preparation.
