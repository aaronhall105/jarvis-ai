# Install Jarvis v17.2.0-r1

Prerequisites:

- Jarvis Core Voice Session Engine v17.0.3.
- A non-empty `OPENAI_API_KEY` in `~/jarvis/.env` with API billing enabled.
- Working Docker Compose deployment at `~/jarvis`.

Upload `jarvis-realtime-voice-v17.2.0-r1-core.tar.gz` to `/home/aaron/jarvis`, then run:

```bash
cd ~/jarvis && \
tar -xzf jarvis-realtime-voice-v17.2.0-r1-core.tar.gz && \
chmod +x tools/install_realtime_voice_v17_2_0_r1.sh && \
./tools/install_realtime_voice_v17_2_0_r1.sh
```

The installer prints a mobile voice token. Store it securely and enter it into the v17.2.0-r1 APK. Do not share the token in screenshots or chat.

The installer does not modify Home Assistant.
