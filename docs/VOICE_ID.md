# Jarvis Voice ID — production v1

Jarvis Voice ID adds text-independent, multi-user speaker recognition to the existing Home Assistant Voice Preview / Voice PE realtime path.

## Voice commands

- **“Jarvis, learn a new voice.”** — fast guided enrollment.
- **“Jarvis, who do you recognise?”** — list enrolled people.
- **“Jarvis, relearn my voice.”** — replace your current profile.
- **“Jarvis, relearn Amber's voice.”** — allowed for that person or Aaron/admin.
- **“Jarvis, forget Amber's voice.”** — Aaron/admin only.

Enrollment asks for a name and five phonetically varied sentences. It normally takes about 20–40 seconds. Audio level, clipping, minimum speech length and cross-sample consistency are checked before the profile is accepted.

Voice ID stores ECAPA speaker embeddings and profile metadata in `speaker-data/jarvis_speakers.db`. Raw enrollment audio is not persisted by this service. Existing diagnostic raw WAV capture in the realtime bridge is disabled by the production Compose override.

## Identity behavior

Each normal Voice PE utterance is scored against every enrolled profile. A match must clear both a similarity threshold and a top-1/top-2 ambiguity margin. Unknown or ambiguous voices become `Guest` rather than being forced to the closest person. The selected identity is passed through the existing `user_id` / `user_name` metadata, so Jarvis's existing per-user conversation and memory scoping can use it.

Very short follow-ups such as “yes” may reuse a high-confidence identity from the previous 45 seconds; a genuine ambiguous or below-threshold biometric result never inherits that identity.

## Security

Voice recognition is convenience identity, not a password. Do not make it the sole factor for door unlocking, purchases, secret disclosure or other high-impact actions. Unknown voices do not inherit Aaron's identity. The sidecar port binds to localhost only.

## Legacy profile migration

On first startup, if there is no Voice ID database, the service looks only in known existing Aaron enrollment directories under `speaker-data`. If it finds at least three usable reference WAVs, it imports them into the new multi-user profile database automatically. Otherwise enroll Aaron normally through Voice Preview.

## Optional adaptation

Unattended automatic adaptation is deliberately disabled in production v1 to prevent profile poisoning. A user can refresh a profile at any time with “Jarvis, relearn my voice,” which replaces it using a fresh quality-checked enrollment.

## Local admin

```bash
python3 tools/voice_id_admin.py status
python3 tools/voice_id_admin.py list
python3 tools/voice_id_admin.py forget amber
```
