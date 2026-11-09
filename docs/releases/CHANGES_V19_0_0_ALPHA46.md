# Jarvis 19.0.0-alpha46 — approval recovery

Phone/Wear versionCode: 190480.
Package: `com.aaron.jarvisvoice`.

Includes the merged PR #129 expired-approval handling
changes in the Android client, Developer Gateway and
protected development controller.

Expired or unavailable approvals must not be presented
as valid authorization. Existing signing identity,
Android application data and Wear protocol are retained.

No storage migration or preference reset is required.

Production signer SHA-256:
009fd523f27cf94eb98917e17670804897e6378e5eccf1ce3ead680721691aac

This release does not activate Guardian, authorize
autonomous development, or establish the original
HTTP 403 rejection cause.
