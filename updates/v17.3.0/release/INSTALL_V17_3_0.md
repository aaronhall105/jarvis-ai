# Install Jarvis v17.3.0

Prerequisites:

- Jarvis Realtime Voice v17.2.0-r1 is installed and healthy.
- `OPENAI_API_KEY` is configured in `~/jarvis/.env` with API billing enabled.
- Docker Compose deployment is working at `~/jarvis`.

Upload `jarvis-unified-voice-v17.3.0-core.tar.gz` to `/home/aaron/jarvis`, then run:

```bash
cd ~/jarvis && \
tar -xzf jarvis-unified-voice-v17.3.0-core.tar.gz && \
chmod +x tools/install_unified_voice_v17_3_0.sh && \
./tools/install_unified_voice_v17_3_0.sh
```

The installer:

- backs up Core voice files, the Android project, `.env` and Android workflows;
- upgrades the Core voice proxy to the unified-brain architecture;
- installs the v17.3.0 Android project and single build workflow;
- rebuilds and validates Jarvis Core;
- makes no Home Assistant integration changes.

The existing mobile voice token is preserved and printed again. Keep it private.

After GitHub Actions builds the APK, Android may require the v17.2.0 debug APK to be uninstalled once because the earlier workflow used a different ephemeral signing certificate. Settings will need to be entered again after that uninstall. Later v17.3.0 workflow builds cache their debug signing key, although a proper private release-signing key remains the long-term solution.
