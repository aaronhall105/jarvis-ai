# Jarvis 19.0.0-alpha40 — Phase 1 observe-only candidate

This candidate adds a monochrome Jarvis Development dashboard, optional durable
health-evidence planning, and protected Guardian/broker tooling. It does **not**
enable autonomous development, coding, merges or releases.

## Phase 1 scope

- Authenticated optional observer API, disabled by default; separate private
  roadmap, health-only evidence, honest idle and stale/unknown capability states.
- No automatic research calls or engineering executor. Research adapter provenance
  is tested; live research is not claimed when no useful question exists.
- Android dashboard, public-only Keystore enrollment export, signed STOP route.
  Phase 1 UI/API cannot activate autonomy; RESUME is rejected before broker IPC.
- Root-owned Guardian, isolated autonomy identity, fixed-operation broker,
  protected ledger/audit and explicit local operator enrollment tools. Source
  installation is separate from product release; no automatic host upgrade.
- Recovery and policy tests. Core broker release support is conservatively limited
  to compatible asset-only changes, never an unrestricted deployment capability.

## Compatibility and limits

Phone/Wear versionCode `190420` continues the established +10 sequence from
alpha39 `190410`; package, signer, Core application version and realtime protocol
are unchanged. Core product metadata is aligned for release verification only;
this candidate does not require or authorize a running Core redeployment.

The existing Guardian must remain STOPPED. The optional observer is OFF by default
and needs an explicitly approved observe-only gateway setup for a live dashboard.
No real phone key is enrolled yet. Physical dashboard, signed STOP, production
artifact and same-key/higher-version recovery-build evidence remain release gates.
No uninstall, data reset, signing replacement, credential export or automated
activation is part of this candidate. See `docs/autonomy-phase1-certification.md`
for the evidence matrix and approval boundaries.
