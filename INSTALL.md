# Installing Jarvis AI

These instructions install the current `v19.0.0-alpha13` Jarvis Core and
describe where to obtain the Android client.

## Requirements

- Ubuntu or another Docker-capable Linux host
- Git
- Docker Engine with Docker Compose
- A configured model provider
- Home Assistant URL and long-lived access token when smart-home control is used
- Android device for the mobile assistant client

## 1. Clone the current branch

```bash
git clone \
  --branch jarvis/unified-production \
  https://github.com/aaronhall105/jarvis-ai.git

cd jarvis-ai
```

## 2. Configure the Core

Create the private environment file:

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env` and provide the settings required by your deployment. Do not commit
this file.

The Compose service bind-mounts:

```text
./config -> /app/config
./data   -> /app/data
./logs   -> /app/logs
```

Back up `data/` before major upgrades because it contains persistent Jarvis
state and databases.

## 3. Start Jarvis Core

```bash
docker compose up -d --build
```

Verify container and API health:

```bash
docker compose ps
curl -fsS http://localhost:8000/health
```

The default API address is:

```text
http://<jarvis-host>:8000
```

Use a trusted private network, VPN or authenticated reverse proxy for remote
access. Do not expose an unauthenticated Core directly to the public internet.

## 4. Install the Android client

Download the APK from the
[`v19.0.0-alpha13` GitHub prerelease](https://github.com/aaronhall105/jarvis-ai/releases/tag/v19.0.0-alpha13).

On Android:

1. Permit installation from the browser or file manager used to open the APK.
2. Install the APK over the existing Jarvis application when upgrading.
3. Open Jarvis and configure the local and remote Core endpoints.
4. Grant microphone and notification permissions.
5. Select Jarvis as the default digital assistant when assistant-button support
   is required.
6. Exclude Jarvis from aggressive battery optimisation for reliable wake-word
   recovery.

Android requires an ongoing foreground-service disclosure while continuous
microphone capture is active. The dedicated wake notification channel is
configured as silent and low priority.

## 5. Home Assistant integration

Copy the integration directory into Home Assistant's
`custom_components` directory:

```text
home_assistant/custom_components/jarvis_core_conversation/
```

The resulting Home Assistant path should be:

```text
/config/custom_components/jarvis_core_conversation/
```

Restart Home Assistant, add the Jarvis Core Conversation integration and provide
the reachable Core endpoint.

## Updating

```bash
cd ~/jarvis
git fetch origin jarvis/unified-production
git pull --ff-only origin jarvis/unified-production
docker compose up -d --build
curl -fsS http://localhost:8000/health
```

Use `--ff-only`; do not force-reset a checkout containing uncommitted work.
