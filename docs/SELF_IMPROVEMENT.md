# Jarvis Self-Improvement Engine v14

Jarvis v14 records failures and corrections, prepares fixes in isolated Git
worktrees, runs regression/security tests, starts a hardened candidate container,
and waits for Aaron's explicit approval before deploying Python source changes.

## Trust boundaries

The live Jarvis container never edits its own source tree and never receives the
Docker socket. A separate host user service performs candidate work. Generated
patches can only touch allow-listed application/test files and cannot edit `.env`,
credentials, Docker configuration, systemd, GitHub workflows or the improvement
policy itself.

## Lifecycle

1. Jarvis records a failed or corrected interaction.
2. Explicit corrections are queued immediately; repeated failures are queued after
   the configured threshold.
3. The host worker creates a Git worktree and a dedicated branch.
4. A coding model proposes a bounded unified diff.
5. The worker enforces path, size and dangerous-code policies.
6. Compilation, pytest, Ruff, Bandit, dependency audit and candidate-container
   health checks run.
7. A passing candidate receives a six-digit approval code.
8. Aaron may approve, reject or request deployment through authenticated Assist.
9. Deployment uses a fast-forward Git merge, Docker rebuild and health/log checks.
10. A failed deployment automatically resets to the previous Git commit and rebuilds.

## Natural commands

- `Show self-improvement status.`
- `Show recorded mistakes.`
- `Prepare a fix for the last mistake.`
- `Approve improvement 12 code 123456.`
- `Deploy improvement 12 code 123456.`
- `Reject improvement 12.`
- `Rollback improvement 12 code 123456.`
- `Emergency stop self-improvement.`
- `Resume self-improvement.`

Only Aaron's authenticated Home Assistant administrator account can use these
commands. Amber's corrections may still be recorded as evidence.

## GitHub protection

Protect `main`, require pull requests, require Jarvis CI and CodeQL checks, require
Aaron's review, dismiss stale approvals, and disallow force pushes/deletion. Enable
GitHub environments with required approval for any future remote deployment job.

## Emergency stop

Say `Emergency stop self-improvement`, or create:

```bash
touch ~/jarvis/data/self_improvement.disabled
systemctl --user stop jarvis-improver
```

Jarvis Core continues running normally.
