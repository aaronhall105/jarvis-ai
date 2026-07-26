# Validation performed for v14

The package was validated locally against the supplied v13 codebase.

## Passed

- Python compilation for:
  - `bridge/app/self_improvement.py`
  - `bridge/app/config.py`
  - `bridge/app/main.py`
  - `tools/self_improvement_worker.py`
- Shell syntax for the installer and worker wrapper.
- Full `bridge/app` compile pass.
- FastAPI application import with Core version `2.1.0`.
- Self-improvement database initialisation.
- Interaction evidence recording and redaction.
- Explicit correction detection.
- Repeated-failure grouping.
- Candidate queue creation.
- Six-digit approval-code checks.
- Aaron-only administration rules.
- Emergency disable/resume behaviour.
- Strict path allow-list and forbidden-path policy.
- Patch size/file-count enforcement.
- Dangerous-code pattern rejection.
- Existing Jarvis regression suite.
- New self-improvement and worker-policy tests.

## Not live-tested in this environment

The following require Aaron's real Ubuntu/Docker/OpenAI/GitHub environment and
must be verified after installation:

- A real coding-model patch generation request.
- A real candidate Docker build using Aaron's Docker daemon.
- A real GitHub pull request.
- A real approved production deployment and automatic rollback.
- Home Assistant mobile notification delivery from the host worker.

No unverified candidate was deployed to Aaron's system while this package was
built.
