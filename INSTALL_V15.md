# Install Jarvis Proactive Action Orchestrator v15

This update runs inside the existing Jarvis Core container. It does not replace
the Home Assistant conversation integration or the v14 improvement worker.

## Install from the package

Upload `jarvis-proactive-action-orchestrator-v15-core.tar.gz` to
`/home/aaron/jarvis/`, then run:

```bash
cd ~/jarvis

tar -xzf jarvis-proactive-action-orchestrator-v15-core.tar.gz

chmod +x tools/install_proactive_v15.sh
./tools/install_proactive_v15.sh
```

The installer:

1. Saves the current Dockerfile in `backup/proactive-orchestrator-v15/`.
2. Installs the v15 Dockerfile entry point.
3. Adds missing `.env` defaults without replacing existing values.
4. Compiles the new Python modules.
5. Runs the v15 tests when pytest is available.
6. Rebuilds Jarvis Core.
7. Displays Core and proactive status.

## Verify

```bash
cd ~/jarvis

docker compose ps

curl -s http://localhost:8000/health
printf '\n'

curl -s http://localhost:8000/api/proactive/status
printf '\n'

curl -s 'http://localhost:8000/api/proactive/alerts?limit=10'
printf '\n'

docker compose logs --tail=200 jarvis-core
```

Expected proactive status includes:

```json
{
  "version": "15",
  "enabled": true,
  "running": true,
  "last_error": null
}
```

## Safe functional checks

Ask Jarvis:

```text
Show active alerts.
```

A real alert can then be acknowledged with:

```text
Thanks.
```

or snoozed with:

```text
Remind me again in ten minutes.
```

Do not deliberately trigger smoke, gas, moisture or security sensors merely to
test the system.

## Roll back to the previous Core entry point

```bash
cd ~/jarvis

cp backup/proactive-orchestrator-v15/bridge_Dockerfile.before-v15 \
  bridge/Dockerfile

docker compose up -d --build
```

The v15 source files and `data/jarvis_proactive.db` may remain; they are not
loaded when the Dockerfile starts `app.main:app` again.
