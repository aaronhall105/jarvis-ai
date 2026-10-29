# Jarvis 19.0.0-alpha43 — Developer conversation recovery candidate

Release preparation only: not yet production-signed, published or installed.

## What changes on the phone

- A confirmed deleted Developer conversation no longer traps session restoration.
  Jarvis forgets only that stale selection and lets you choose or start a chat.
- Delayed or unrelated errors cannot clear a newer conversation selection.
- Saved history, settings and authentication remain intact. Queued work is not
  silently submitted into another chat, and request failures are distinguished
  from an actual reconnect.
- Includes the merged Developer history/progress display fixes since alpha40.

The compatible PR104 gateway is already deployed; this candidate adds no gateway
implementation changes and does not authorize another deployment. PR100–104
gateway safeguards remain included in the source lineage.

## Identity and continuity

Phone and Wear use `19.0.0-alpha43` / `190450`, package `com.aaron.jarvisvoice` and
the existing production signing policy. Alpha41/190430 remains reserved by PR98;
the documented alpha42/190440 return plan is also avoided. Neither workstream is
merged or changed by this candidate. Wear behavior and shared protocol are unchanged.

Product release metadata is synchronized as required by the release contract.
Core application version stays `3.7.0`, realtime protocol stays `2`; no running
Core deployment is required or authorized. Storage, encryption aliases, Android
Keystore operator identity and manifest components are unchanged from alpha40.
Do not uninstall, clear data, downgrade or replace signing credentials.

## Boundaries

Guardian stays STOPPED/trusted; the optional observer stays disabled by default.
This maintenance release does not certify Phase1 or activate autonomous development.
Live observer/dashboard, real operator enrollment and signed STOP/denial acceptance
are separate work. See `docs/android-alpha43-release-readiness.md` for approval,
publication, physical acceptance and higher-code recovery requirements.
