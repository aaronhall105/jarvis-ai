# Security boundaries

Jarvis v14 is intentionally supervised rather than an unrestricted self-modifying
agent.

## Never granted to the live container

- Write access to the Git source tree.
- Docker socket access.
- Host shell access.
- Permission to deploy generated code.

## Never editable by generated patches

- Secrets and `.env`.
- Authentication and token files.
- Docker/systemd/GitHub control files.
- Dependency manifests.
- The improvement policy or worker.
- Persistent user data and memories.

## Deployment approval

A candidate needs its ID and random six-digit code. Voice/chat deployment is
limited to Aaron's authenticated administrator account. REST write endpoints use
a separate random admin token generated during installation.

## Emergency controls

```bash
touch ~/jarvis/data/self_improvement.disabled
systemctl --user stop jarvis-improver
```

This stops self-improvement without stopping normal Jarvis Core operation.
