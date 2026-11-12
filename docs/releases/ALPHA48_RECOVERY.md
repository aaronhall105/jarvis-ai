# Alpha48 publication recovery audit

- Canonical source base: `6272a9afe142dd51d9acd53c7bbd771bc4a12d4d`.
- Failed Alpha47 Actions run: `43086713281`, job `130752889068`.
- Signed Phone and Wear APK builds and initial signature checks succeeded.
- `tools/publish_android_ota.py` failed at local signing-report validation
  with `PublicationError: signer_report_mismatch`, before creating a draft.
- Saved reconciliation artifact: `13886837523`, phase `prepared`.
- No Alpha47 versioned release was visible and the feed remained Alpha46.
- The strict parser expected `Signer #1` but newer Build Tools may print
  scheme-qualified `V3.0 Signer:` or `V2 Signer:`. The exact failed-run
  signing-report bytes were not retained, so this remains the supported
  diagnosis rather than a claim of byte-for-byte reproduction.

The repair pins verification Build Tools to 36.0.0 and regression-tests both
recognised report forms, a single production certificate and invalid reports.
It preserves all other immutable-release and mutable-feed controls.

Alpha47 is a consumed tag: do not rerun, delete, move or reuse it.
This candidate **does not merge, sign, tag, publish, promote or install**.
After protected checks and independent approval, the owner must separately
approve the exact Alpha48 canonical merge SHA and OTA publication.
