# Jarvis 19.0.0-alpha30 — OTA recovery and safer multi-inbox cleanup

- Includes the approved Email Assistant compound Gmail and Outlook cleanup
  improvements prepared for alpha29.
- Keeps Gmail and Outlook candidate sets separate and requires confirmation of
  the exact frozen messages before any recoverable cleanup action.
- Uses Gmail Bin and Outlook Deleted Items; cleanup never permanently deletes
  mail.
- Distinguishes handled provider/action failures from genuine Jarvis Core
  transport failures in the Android client.
- Repairs the stable-signed OTA workflow so Android setup no longer requests
  Google's removed legacy SDK `tools` package.
- Retains the existing stable signing identity, package ID, release checks,
  compiled-product inspection, checksums, and OTA channel validation.

The immutable alpha29 tag failed during Android SDK setup before signing or
publication. No alpha29 APK was released and the OTA channel remained on
alpha28. Alpha30 is the recovery release containing those approved changes.
