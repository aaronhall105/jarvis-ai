# Jarvis 19.0.0-alpha23 — single unified product baseline

This release uses the existing phone and Watch source proven by GitHub Actions
artifact `9714544550` at unified-production commit
`1eb4c5913a3e69213ec45ad726bebb45779e3c01`, while retaining every later
Core and Developer security correction.

## Included

- Restores the exact phone UI/navigation/resource lineage represented by
  `jarvis-phone-19.0.0-alpha21-release.apk` SHA-256
  `5e3961eb3484c814a301f5385f11f3db890ad6d66a9ef79e933eb3209af40e16`.
- Preserves the exact Watch application lineage represented by
  `jarvis-watch-19.0.0-alpha21-release.apk` SHA-256
  `98e1f543d1afcbf267dc465a84959aaa77e2cf76913cceab596aa3e0e41efe91`.
- Retains current conversation history, safe chat deletion, Improvements,
  Developer/Codex, Integrations, Google OAuth routing, realtime recovery,
  voice interruption, assistant/overlay, wake word, endpoint failover, Watch
  bridge, Tile and shared protocol functionality.
- Keeps the unified Brain, Memory, Home Assistant grounding, External Agent,
  planner, capability registry, action receipts, durable jobs, proactive and
  vision systems, Google integrations and security hardening.
- Records one source SHA for Core, phone and Watch in the release manifest and
  exposes exact Core and Developer deployment provenance.
- Moves OTA channel metadata into release-hosted assets, eliminating the
  separate OTA development branch.

Google remains **Setup Required** until OAuth credentials are securely
configured in Jarvis Core. No Google password is entered into Jarvis.
