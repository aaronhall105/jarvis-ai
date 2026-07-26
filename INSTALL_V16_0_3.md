# Install Jarvis v16.0.3

Upload the archive by SFTP to `/home/aaron/jarvis`, then run:

```bash
cd ~/jarvis
tar -xzf jarvis-spoken-progress-v16.0.3-core.tar.gz
chmod +x tools/install_temporal_v16_0_3.sh
./tools/install_temporal_v16_0_3.sh
```

After Jarvis Core reports v16.0.3, update the Home Assistant conversation integration with the command printed by the installer. Home Assistant Core restarts after that integration update.
