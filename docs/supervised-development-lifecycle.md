# Supervised development lifecycle contract

This controller is durable lifecycle/state orchestration. The
`SupervisedDevelopmentPipeline` joins it to the existing development worker and
review interface, but execution is admitted only by the fixed protected-broker
socket. The current production broker does not yet install that development
admission endpoint, so production execution remains fail-closed.

The existing certified Phase 2 harness remains responsible for provider budget,
credential separation, deadlines, structured JSON validation, source checksums,
allow-list/path/symlink checks, atomic edit application, tests, static checks,
cleanup and generated diff evidence. A future trusted adapter must verify those
results before submitting evidence here.

The controller records an approval request, then asks the root-owned
`/run/jarvis-autonomy-broker/development-admission.sock` endpoint to attest the
exact task identity, source SHA, allow-list, provider, model, call limit, cost
cap and deadline. No caller-supplied callback, database label or fixture can
override this. The broker must authenticate the enrolled owner and bind its
receipt to the immutable task parameter hash before returning an attestation.
Review evidence is recorded only as a terminal handoff; it never grants
execution authority.

## Isolated vertical slice

`GovernorAdapter` accepts only a queued, bounded improvement candidate and
constructs immutable one-call, one-dollar, 120-second task parameters.  The
`DevelopmentAdmissionBroker` verifies a domain-separated owner signature,
stores a replay-safe approval in the existing ledger, and issues at most one
worker admission only while the supplied disposable Guardian is trusted and
active.  The controller exchanges that admission over its fixed Unix socket;
there is no caller-selected verifier or endpoint.
Owner approval reaches this broker only through the existing root-owned
operator service, which forwards the domain-separated signed
`approve_development` payload. The worker peer cannot submit approvals.

`TrustedReviewRunner` is read-only.  It recomputes the checkout HEAD, detects
untracked files and source drift, derives the binary Git diff and its SHA-256,
enforces the allow-list, and requires successful test and security records.
It can only produce evidence for `REVIEW_REQUIRED`; it has no Git, merge,
deployment, or Guardian authority.  A disposable integration test exercises
Governor selection → signed admission → worker edit → local verification →
`REVIEW_REQUIRED` through these interfaces.

The protected installer now carries the development broker module and a
dedicated root-owned `development-admission` service/socket with the same
systemd hardening and manifest pinning as the existing broker roles.  It uses a
separate durable ledger under the broker state directory and the existing
enrolled operator public-key policy.  Installation is still a separately
authorised host operation; until it is performed and verified, production
execution remains fail-closed.  No provider call, production write, or
autonomous activation is part of this milestone.

`REVIEW_REQUIRED` is a human handoff only. It grants no commit, push, PR, merge,
deployment, release, or Guardian authority. The pipeline is provider-free in
tests; the existing worker adapter is ready for a broker-admitted candidate,
but the protected broker socket and its systemd installation require a separate
security-reviewed deployment approval. Independent review must be supplied by
the trusted review runner, not by model output.
