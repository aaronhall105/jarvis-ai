# Jarvis 19.0.0-alpha49 — supervised development from the owner dashboard

Phone/Wear versionCode: 190510. Package: `com.aaron.jarvisvoice`.

Adds a native route from ordinary Jarvis chat and voice development requests to
the existing owner dashboard. Request identities survive reconnects. The owner
can inspect the source-bound candidate, separately confirm task preparation,
development approval and task-bound Guardian activation, and review the exact
proposal diff with verified test and security evidence. Pull-request publication
requires a separate signature bound to that proposal; it never authorizes merge
or deployment. Uncertain operations are reconciled instead of automatically
replayed. Missing protected integration remains an explicit blocked state.

No storage migration or preference reset is required. Existing conversations,
settings, Android Keystore identities, application ID, signing certificate,
permissions, Wear and Home Assistant contracts are retained. New local request
metadata uses a separate private preference file without replacing existing data.
Core API remains 3.7.0 and realtime protocol remains 2.

Production signer SHA-256:
`009fd523f27cf94eb98917e17670804897e6378e5eccf1ce3ead680721691aac`.

This is release preparation. A reviewed canonical merge, approved immutable tag,
production signing, independent certificate/version verification, and the normal
OTA channel publication are still required. No APK has been installed or published
by preparing this candidate. Compatible Core, gateway and protected services must
be deployed through their separately authorized procedures before the new controls
can be used. Device acceptance and a real supervised coding task remain separate
operational checks; passing local tests does not establish live autonomy.
