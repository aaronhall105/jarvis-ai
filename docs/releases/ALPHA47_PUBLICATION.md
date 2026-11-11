# Alpha47 publication approval and recovery

This candidate is based on `19d4158b627bc781a07bc879aa2095126b681834`.
Its proposed immutable tag is `v19.0.0-alpha47`, versionCode `190490`.
The tag must ultimately point to the **reviewed canonical merge commit**, not
the pre-release base or a feature-branch commit. API 3.7.0 and realtime 2 stay
unchanged. No APK signing, publication, feed update or installation is authorised
by preparing this PR.

## Two different release objects

- Versioned product releases are immutable. The workflow creates a new draft,
  attaches all ten verified Phone/Wear assets, verifies their downloaded bytes,
  then publishes that draft exactly once. It requires `immutable: true`, exact
  source/tag identity, complete asset inventory and GitHub SHA-256 digests before
  touching any channel. An existing release, including a draft, is a blocker;
  there is no upload-overwrite or existing-release editing fallback.
- Existing `jarvis-alpha-feed` is a **mutable pointer**, not a product release.
  Only its manifest is replaced, after verifying the immutable product. Its
  release ID and previous manifest bytes/asset ID are pinned at preflight and
  rechecked before promotion. The new versionCode must be strictly greater.
  Alpha changes only alpha; beta can advance beta/alpha; stable can advance all
  three. Every targeted channel must already exist and report `immutable:false`.
  Missing or immutable channels are never automatically created/recreated.

Repository immutability only affects future published releases, so the existing
mutable alpha pointer can remain usable. Beta/stable pointers are currently
absent: those releases fail closed until separately reviewed mutable channel
hosting is established. Do not create them under immutable-release enforcement
and expect them to be replaceable.

## Separate operator prerequisites (not performed by this PR)

The read-only check during preparation returned `enabled:false` and
`enforced_by_owner:false`. GitHub's documented repository API is:

```bash
gh api -H 'X-GitHub-Api-Version: 2026-03-10' \
  repos/aaronhall105/jarvis-ai/immutable-releases
```

This GET requires repository **Administration: read**, which the ordinary
contents-write workflow token does not supply. Provision a narrowly scoped
read-only repository credential as the Actions secret
`JARVIS_RELEASE_SETTINGS_READ_TOKEN`, using the approved secret-management
procedure without printing its value. The helper passes it only to the setting
GET; publication uses `github.token`. No administration-write token is needed
in the workflow. Missing/denied/malformed setting evidence blocks before signing.

After separate owner authorisation, an administrator may enable the setting:

```bash
gh api --method PUT -H 'X-GitHub-Api-Version: 2026-03-10' \
  repos/aaronhall105/jarvis-ai/immutable-releases
```

Repeat the GET and confirm `enabled:true`; also verify the existing channel:

```bash
gh api repos/aaronhall105/jarvis-ai/releases/tags/jarvis-alpha-feed \
  --jq '{id,tag_name,immutable,draft}'
```

Require `immutable:false`, `draft:false`. Preserve the existing signing secrets
and expected production certificate
`009fd523f27cf94eb98917e17670804897e6378e5eccf1ce3ead680721691aac`.
Restrict tag creation/deletion/force updates and channel writes to approved
release operators. No repository administration setting is changed by this PR.

## After PR review and separately authorised merge/release

In a clean canonical checkout, fetch and record the exact reviewed merge SHA.
Review it explicitly before tagging. Do not blindly tag whatever HEAD happens
to be. These are future operator commands, not part of candidate preparation:

```bash
git fetch origin jarvis/unified-production --tags
test -z "$(git status --porcelain)"
test "$(git branch --show-current)" = jarvis/unified-production
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/jarvis/unified-production)"
git rev-parse HEAD
# Stop: owner must approve the exact SHA above and publication prerequisites.
git tag -a v19.0.0-alpha47 -m 'Jarvis 19.0.0-alpha47'
git push origin refs/tags/v19.0.0-alpha47
```

The fresh-tag push must be non-forced with a zero previous object. Both the live
tag object and dereferenced commit are pinned; canonical drift or tag movement
at any subsequent security fence blocks publication/promotion. Rerun attempts
are refused. GitHub additionally prevents reuse of a deleted immutable release's
tag. The API cannot prove the entire history of a deleted, never-published tag;
tag protection and operator version reservations remain necessary. The workflow
does not silently create tags or change protection rules.

## Partial failure and reconciliation

All release tags share one non-cancelling workflow concurrency group. State is
persisted before draft creation, upload, publish and each promotion, with run ID,
tag, source, release/asset IDs, previous feed digest and original manifest. The state artifact is
retained for 90 days even on failure. No automatic retry or compensation writes
are performed. A failed asset upload leaves a draft; a failed publish response
may mean publication actually succeeded. Inspect the recorded release ID and
server state read-only. Never delete/recreate a version, move its tag, replace
its assets, or rerun to "repair" publication. Use a new reviewed release version
if publication cannot be conclusively reconciled.

Channel replacement is not a transaction: GitHub's upload `--clobber` can remove
the old manifest before upload fails, and REST provides no conditional asset
replacement/CAS. Restrict channel writers to this serialized workflow; external
administrator changes can still race the final check. Preserve the state artifact
with the previous manifest before recovery. If promotion fails, the immutable APKs
remain intact, but a channel may be unavailable or lag. Stop and reconcile the
actual manifest ID/digest before a separately approved recovery. Never promote
an unverified APK or lower/reuse versionCode. In a partial multi-channel promotion,
already advanced channels remain valid; other channels are not rolled back or
silently rewritten. No service, app data, credential, Guardian or deployment
rollback is involved.

## Authoritative GitHub references

- [Repository immutability REST API](https://docs.github.com/en/rest/repos/repos#check-if-immutable-releases-are-enabled-for-a-repository)
- [Enable immutability; existing releases remain unchanged](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/establish-provenance-and-integrity/prevent-release-changes)
- [Draft publication, asset/tag protection and deleted-tag reuse](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)

Local tests exercise a stateful disposable GitHub boundary, not live publication.
Actual signed APK and immutable-release acceptance occurs only in the separately
authorised tag-triggered run. No local test is evidence of a published Alpha47.
