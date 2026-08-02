# Install Jarvis v16.3.0

Upload `jarvis-multi-step-routines-v16.3.0-core.tar.gz` to `/home/aaron/jarvis`.

Run on the Ubuntu Jarvis computer:

```bash
cd ~/jarvis && \
tar -xzf jarvis-multi-step-routines-v16.3.0-core.tar.gz && \
chmod +x tools/install_multi_step_routines_v16_3_0.sh && \
./tools/install_multi_step_routines_v16_3_0.sh
```

This is a cumulative Core package. It can upgrade a working v16.1.0 installation directly and includes v16.2.0 Conditional Actions.

No Home Assistant Terminal installation is required. Assist remains v1.5.4.
