# Historical validation — Self-Improvement v14

This report is preserved for traceability. It describes validation of the v14
self-improvement package against the supplied v13 codebase and is **not** the
current whole-product validation status.

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

## Not live-tested in that environment

The following required the deployment owner's real Ubuntu/Docker/OpenAI/GitHub
environment and were left for post-install verification:

- a real coding-model patch generation request;
- a real candidate Docker build using the host Docker daemon;
- a real GitHub pull request;
- a real approved production deployment and automatic rollback;
- Home Assistant mobile notification delivery from the host worker.

No unverified candidate was deployed while the v14 package was built.
