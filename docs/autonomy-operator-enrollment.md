# Android operator enrollment — local operator ceremony

This is not a broker/Guardian reinstall, an autonomy activation, or routine
development approval. It authorizes one Android public identity for the existing
signed operator protocol. No enrollment API or passwordless sudo grant is added.
The autonomous account, Governor, Core and structured Developer tools cannot run
this privileged interactive ceremony. Local root remains trusted; a host root
compromise is not prevented by a Python caller check.

## Current evidence and prerequisites

The installed host is STOPPED/trusted. No operator key is currently enrolled.
The real Samsung SM-G996B is now connected through authorized wireless ADB. The
local feature APK has not been installed: its debug signing identity differs from
the phone's production signer. Installation was stopped before any app/data change.
See `autonomy-governor-progress.md` for exact certificate evidence. A matching
production-signed feature build is required before exporting the real phone key.
Do not claim that software fixtures enroll Aaron's phone.

Before enrollment, install the feature build only through the normal compatible
update procedure after checking the installed package/signature. Do not clear app
data, uninstall, change signing identity, or use a mismatched debug signature to
force an update. Release/OTA publication is a later feature milestone.

Open **Jarvis Development → Set up operator controls**. This works even if the
observe-only API is not deployed. The app creates/reuses the stable alias
`jarvis.autonomy.operator.v1` in Android Keystore. Its private key is used only for
signing; only public SPKI, its SHA-256 fingerprint and a proof of possession are
copied. Ordinary app restarts and same-identity updates retain the alias. App data
clearing/uninstall or Keystore loss requires explicit replacement, never automatic
re-enrollment.

Copy the public request to the local Ubuntu terminal. It is not a password or
API key, but there is no need to send it to chat, logs, Core, research or GitHub.
Keep the phone's fingerprint visible to compare independently.

## Initial enrollment (operator executes locally, not the agent)

Once the phone's public request is available, use this single-line command:

```sh
sudo bash /home/aaron/jarvis-autonomous-development-worktree/tools/enroll_autonomy_operator.sh enroll
```

Enter a Linux password only in the local terminal if prompted. Then paste the
one-line **public** request into the local tool, and type the exact fingerprint
shown on the phone. The tool never asks for a private key, API credential or token.
Proof of possession is not itself authorization: local privileged operator action
and fingerprint confirmation are required. An existing different key is refused.

This tool modifies only:

- `/etc/jarvis-autonomy/broker.json`: `operator_public_keys` only.
- `/var/lib/jarvis-autonomy-broker/installed.json`: updated policy hash only.
- `/var/lib/jarvis-autonomy-guardian/installed.json`: policy/broker manifest hashes only.
- `/var/lib/jarvis-autonomy-broker/records/operator-enrollment.json`: protected transaction/recovery record.
- `/var/lib/jarvis-autonomy-broker/records/audit.jsonl`: append-only hashed enrollment events.

It closes the fixed `jarvis-autonomy-broker-operator.socket` and `.service` during
the transaction so revoked keys cannot remain cached, then reopens that socket
only after integrity verification. It does not restart Core, Developer, Docker,
the Guardian, or the autonomy worker. No package, group, credential or executable
is installed. Guardian state/generation is not modified and must remain STOPPED.
The original policy and manifests are retained in a root-only transaction record.
It contains public configuration, not private keys or credential values.

## Replacement and revocation

Use the same reviewed script with `replace OLD_SHA256_FINGERPRINT` or
`revoke OLD_SHA256_FINGERPRINT` instead of `enroll`. The exact currently authorized
old fingerprint is required. Replacement also requires the new phone's public
request and explicit fingerprint confirmation. Revocation removes the public
identity; host CLI status/stop remains independent. No second key silently
replaces or coexists with the first. Exporting another request from the same
Keystore identity does not rotate the identity.

## Interrupted ceremony / recovery

Ordinary recoverable failures restore original metadata, preserve STOPPED and
leave the operator IPC closed. A pending/uncertain journal blocks re-execution.
Do not delete that journal or run the broker installer/manifest repair as a
workaround. A trusted operator must inspect the protected journal and audit,
compare recorded originals/target hashes with the three installed metadata files,
and reconcile the exact interrupted transaction. Unknown state needs root-reviewed
recovery, not automatic replay. Host CLI STOP remains the independent fallback.
After a completed metadata transaction interrupted only before socket reopening,
repeating the exact enrollment can reopen the fixed socket without changing keys
or activating autonomy.

## Wire contract and verification scope

Control v1 fields: `action`, `expires_at`, `generation`, `key_id`, `request_id`,
`version`, plus `signature`. UTF-8 sorted compact JSON, signed with the prefix
`jarvis-autonomy-control-v1\n`. The contract supports stop/resume; broker also supports
signed status. Phase 1 UI exposes only STOP and its API rejects RESUME before IPC,
even if signed. Host operator activation remains a separate later boundary.
Generation is exact, expiry at most 60 seconds, request ID canonical
UUID. Signature: P-256 ECDSA/SHA-256, ASN.1 DER signature, standard Base64 without
line wrapping. Key ID: lowercase SHA-256 of DER SPKI.

Enrollment uses a separate `jarvis-autonomy-enrollment-v1\n` prefix and fields
`key_id`, `nonce` (UUID), `public_key` (Base64 DER SPKI), `version`, plus `signature`.
It does not authorize control, carry a shared resume secret, or change Guardian
generation. Shared public-only vectors exercise Python/Java canonicalization and
signature verification. Their ephemeral fixture private key was not retained.
Unit/rehearsal success is not physical Keystore or live signed-host certification.

Completed signed-control request replay returns a stored receipt without another
transition; altered reuse, stale generation, expired/unknown-key requests and
uncertain operations are denied. Android refreshes authoritative status rather than
optimistically displaying the receipt as current authority. A signed RESUME test
that actually activates the host belongs to operator-authorized Phase 2, not to
an unattended Phase 1 probe.
