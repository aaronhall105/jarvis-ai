# Jarvis 19.0.0-alpha50 — OTA sandbox verification repair

Phone/Wear versionCode: 190520.
Package: `com.aaron.jarvisvoice`.

Carries forward the Alpha49 supervised-development Android,
Core and Gateway capabilities without altering their runtime
security or approval contracts.

Repairs the Android OTA release workflow by reusing the
existing tested Bubblewrap and restricted AppArmor setup
from the protected Jarvis CI workflow. Real sandbox tests
remain mandatory. No sandbox or security test is disabled.

Alpha49's tagged source and failed release evidence remain
unchanged. Alpha50 is a separate release candidate.

No storage migration is required. Existing conversations,
settings, Keystore identities, signing identity, integration
accounts, Home Assistant settings and Wear data are preserved.

Core API: 3.7.0.
Realtime protocol: 2.
Production signer SHA-256:
009fd523f27cf94eb98917e17670804897e6378e5eccf1ce3ead680721691aac

Production deployment, signing, OTA publication, physical
installation and live supervised development still require
their respective approval and verification.
