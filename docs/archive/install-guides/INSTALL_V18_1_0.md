# Install Jarvis Assistant v18.1.0

This package can upgrade either Jarvis Unified Voice v17.3.0 or Jarvis Chat v18.0.0.

Upload `jarvis-assistant-v18.1.0-core.tar.gz` to `/home/aaron/jarvis`, then run:

```bash
cd ~/jarvis && \
tar -xzf jarvis-assistant-v18.1.0-core.tar.gz && \
chmod +x tools/install_jarvis_assistant_v18_1_0.sh && \
./tools/install_jarvis_assistant_v18_1_0.sh
```

After GitHub Actions builds the APK, install `jarvis-assistant-v18.1.0-debug.apk`.

In Jarvis Settings, tap **Set Jarvis as default assistant**, select Jarvis, then on Samsung configure:

`Settings → Advanced features → Side button → Long press → Digital assistant`

Keep wake phrase enabled and set Jarvis battery usage to Unrestricted for the best background reliability.
