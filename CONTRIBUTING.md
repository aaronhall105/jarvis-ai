# Contributing

Jarvis is developed as one unified product. `jarvis/unified-production` is the
sole long-lived and default branch for Core, Phone, Watch, release tooling, and
documentation.

## Before changing Jarvis

- Search existing issues and pull requests.
- Start from the current `origin/jarvis/unified-production` head.
- Keep each change bounded to one coherent purpose.
- Identify affected components and shared protocol/API implications.
- Preserve production data schemas and in-place client compatibility unless a
  reviewed migration explicitly requires otherwise.
- Never include credentials, tokens, household data, runtime databases, logs,
  private signing material, or real OAuth values.
- Add regression evidence for confirmed defects and meaningful behavior changes.

## Branch and pull-request workflow

Direct pushes are blocked by branch protection. When a pull request is needed:

1. Fetch and create a temporary branch from `jarvis/unified-production`.
2. Make and validate the bounded change.
3. Open a pull request back to `jarvis/unified-production`.
4. Wait for required Core, quality/security, Android, and CodeQL checks.
5. Resolve review conversations and merge through branch protection.
6. Delete the temporary local and remote branch immediately after merge.

Temporary branches are short-lived implementation vehicles, not permanent
product lines. Do not recreate historical feature branches or establish a
second release/deployment lineage. Never force-push the authoritative branch.

## Validation

Run checks proportional to the changed components. At minimum:

```bash
git diff --check
python tools/verify_product_baseline.py --skip-ref
```

Code changes normally also require relevant Python tests, Ruff, mypy, security
checks, Android/Wear tests or lint, and package/protocol checks. Documentation
changes require reference and Markdown-link validation.

Report physical Phone, Watch, Home Assistant, or deployment validation only when
it actually occurred. Otherwise mark it as required.

## Releases and deployment

Only approved immutable `v*` tags at the current unified-production head may
publish Phone/Watch releases and OTA metadata. Core and Developer production
deployments must use a clean authoritative worktree at the exact remote head.

Do not rotate signing keys, change package identity, recreate persistent data,
or manually advance OTA metadata to bypass a failed gate.
