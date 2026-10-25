# Jarvis 19.0.0-alpha41 — proposed Android recovery candidate

Source PR/checks authorized; NOT APPROVED FOR MERGE, SIGNING OR PUBLICATION.
Recheck release numbering before use.

Restores Phone/Wear application behavior from the signed alpha39 source
42cfa630ce4e81f14f73de457dc3c822c53958a8, retaining package/signing identity
and advancing synchronized versionCode to 190430. Host-side Phase 1 tooling
remains dormant; Guardian STOPPED, observer disabled by default. Core product
metadata alignment does not deploy Core. No database or app-data restoration.

This is a data-preserving forward-version software recovery, not an APK downgrade.
It removes the Phase 1 Android dashboard and reverts the intervening PR #96
Android UI changes as well. Existing conversations/settings/auth/Keystore must
be verified on the real phone; no data-preservation success is claimed yet.

The unchanged package-private operator-contract helper and its shared signature
tests remain, with no production caller, UI or manifest entrypoint. This preserves
host enrollment regression coverage without generating keys or enabling controls.
Storage and Keystore aliases are unchanged. See `docs/autonomy-recovery-review.md`
for explicit tradeoffs, approval boundaries and the higher-code feature-return plan.

Requires its own reviewed source PR, protected checks, explicit canonical merge
and release approval, and the existing canonical-tag CI signing/publishing flow.
Publishing advances the public alpha feed. A later return to the Phase 1 feature
would require another same-key higher-version release; never reuse a tag or code.
