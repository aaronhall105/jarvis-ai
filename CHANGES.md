# Jarvis Self-Improvement Engine v14

## Supervised autonomous improvement

Jarvis can now record mistakes and corrections, group repeated failures, prepare
candidate code fixes and test them without touching the running production code.

## Evidence recorder

Each relevant interaction can store a redacted diagnostic record containing:

- Original user text.
- Interpreted text and intent.
- Success or failure result.
- Verified tool calls.
- Understanding and tone metadata.
- Jarvis-side latency.
- Core version.
- A later explicit correction.

Secrets and credentials are redacted before evidence is stored or sent to a
coding model.

## Persistent improvement database

Stored at:

`/app/data/jarvis_improvement.db`

It contains:

- Interaction evidence.
- Failure records and occurrence counts.
- Candidate status and approval codes.
- Test/security results.
- Deployment and rollback references.
- An append-only audit trail.

## Isolated host worker

The live Jarvis container does not edit source code and does not receive the
Docker socket. A separate unprivileged host-user service performs improvement
work in isolated Git worktrees.

## Candidate lifecycle

1. Record and classify a failure.
2. Queue explicit corrections immediately or repeated failures after the configured threshold.
3. Create a dedicated Git branch and worktree.
4. Ask a coding model for one bounded unified diff.
5. Enforce strict path, size and dangerous-code policies.
6. Run compilation, pytest, Ruff, Bandit and dependency audit.
7. Build and run a restricted candidate container.
8. Run an independent second AI safety/correctness review.
9. Produce a six-digit approval code.
10. Wait for Aaron to request deployment.
11. Fast-forward merge, rebuild and verify health/logs.
12. Automatically reset and rebuild the previous commit if deployment checks fail.

## Safety policy

Generated code cannot modify:

- `.env` or credentials.
- Data, logs or memory databases.
- Docker Compose or Dockerfiles.
- Requirements/dependencies.
- GitHub workflows.
- Systemd files.
- The improvement worker itself.
- The self-improvement security policy.

New shell execution, `eval`, `exec`, unsafe deserialisation, Docker socket access
and direct unapproved network-call patterns are rejected.

## Approval and permissions

- Only Aaron's authenticated Home Assistant administrator account can prepare,
  approve, deploy, reject, rollback, disable or resume improvements.
- Amber's corrections may be recorded as evidence but cannot deploy code.
- Source-code auto-deployment is disabled.
- Candidate generation is limited by daily attempts, file count and changed-line
  budgets.

## Natural commands

- `Show self-improvement status.`
- `Show recorded mistakes.`
- `Show pending improvements.`
- `Record that as a mistake.`
- `Prepare a fix for the last mistake.`
- `Prepare a fix for failure 12.`
- `Approve improvement 12 code 123456.`
- `Deploy improvement 12 code 123456.`
- `Reject improvement 12.`
- `Rollback improvement 12 code 123456.`
- `Emergency stop self-improvement.`
- `Resume self-improvement.`

## API endpoints

Read endpoints:

- `GET /api/improvement/status`
- `GET /api/improvement/failures`
- `GET /api/improvement/candidates`
- `GET /api/improvement/candidates/{id}`
- `GET /api/improvement/audit`

Sensitive write endpoints require `X-Jarvis-Admin-Token`:

- `POST /api/improvement/failures/{id}/prepare`
- `POST /api/improvement/candidates/{id}/approve`
- `POST /api/improvement/candidates/{id}/deploy`
- `POST /api/improvement/candidates/{id}/reject`
- `POST /api/improvement/candidates/{id}/rollback`

## GitHub guardrails

The package adds:

- Jarvis CI workflow.
- CodeQL workflow.
- Dependency review.
- CODEOWNERS.
- Pull-request template.

Repository branch protection still needs enabling in GitHub settings.
