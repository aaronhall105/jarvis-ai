# Jarvis v14 self-improvement architecture

```text
Live Home Assistant request
        │
        ▼
Jarvis Core records redacted evidence
        │
        ├── normal success ──────────────► continue normally
        │
        └── correction/repeated failure
                     │
                     ▼
          Improvement SQLite queue
                     │
                     ▼
      Host-side jarvis-improver service
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
  Isolated Git worktree   Coding-model patch
          │                     │
          └──────────┬──────────┘
                     ▼
       Policy + regression/security tests
                     │
                     ▼
       Restricted candidate Docker container
                     │
                     ▼
        Independent second AI review
                     │
                     ▼
      Candidate summary + six-digit code
                     │
              Aaron approval/deploy
                     │
                     ▼
      Fast-forward merge and Docker rebuild
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
     Health passes          Health fails
          │                     │
          ▼                     ▼
       Deployed        Automatic Git rollback
```

## Separation of duties

### Live Jarvis Core

- Records evidence.
- Groups failure signatures.
- Exposes status and approval commands.
- Never writes source code.
- Never receives Docker daemon access.

### Host worker

- Runs as the ordinary Jarvis Linux user.
- Owns Git worktrees and candidate branches.
- Calls the coding/review model.
- Applies static policy checks.
- Runs tests and candidate containers.
- Performs approved deployments and automatic rollback.

### Aaron

- Reviews candidate summary, risk, changed files and test results.
- Supplies the six-digit code for deployment.
- Can reject, stop or roll back the system.

## Trust model

AI-generated output is treated as untrusted input. It must pass deterministic
allow-list checks, compilation, regression tests, security scans, a restricted
container smoke test and a separate review before it can become deployable.

## Persistence

- Improvement database: `data/jarvis_improvement.db`
- Worktrees and patch artifacts: `.jarvis-improver/`
- Production source history: Git repository
- Runtime service: `jarvis-improver.service`

## Failure containment

- Candidate containers have no network, no extra capabilities, a read-only root
  filesystem and CPU/memory/process limits.
- Production deployment is fast-forward only.
- A changed production base invalidates stale candidates.
- Failed startup, health or log checks trigger automatic rollback.
- The emergency-disable file immediately stops further improvement work.
