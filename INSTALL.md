# Installing Jarvis AI

These instructions install the current source from the
`conversation-engine` branch. At the time of this documentation refresh the
branch identifies itself as `v19.0.0-alpha17`.

Jarvis is an alpha system. Back up persistent data before upgrades and avoid
exposing Jarvis Core directly to the public internet.

## Requirements

- Ubuntu or another Docker-capable Linux host;
- Git;
- Docker Engine with Docker Compose;
- a configured model provider;
- Home Assistant URL and long-lived access token when smart-home control is used;
- an Android device for the mobile assistant client.

## 1. Clone the current branch

```bash
git clone \
  --branch conversation-engine \
  https://github.com/aaronhall105/jarvis-ai.git

cd jarvis-ai
```

## 2. Configure the Core

Create the private environment file:

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env` and provide the settings required by your deployment. Never commit
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

Android source lives under:

```text
android/jarvis-voice-client/
```

Published APKs should be obtained from:

https://github.com/aaronhall105/jarvis-ai/releases

Because this project moves quickly, verify that the APK release identity matches
the intended Core/source release instead of relying on an old hard-coded link.

On Android:

1. permit installation from the browser or file manager used to open the APK;
2. install the APK over the existing Jarvis application when upgrading;
3. open Jarvis and configure the local and remote Core endpoints;
4. grant microphone and notification permissions;
5. select Jarvis as the default digital assistant when assistant-button support
   is required;
6. exclude Jarvis from aggressive battery optimisation when reliable background
   wake behaviour is required.

Android requires an ongoing foreground-service disclosure while continuous
microphone capture is active.

## 5. Home Assistant integration

Copy:

```text
home_assistant/custom_components/jarvis_core_conversation/
```

to:

```text
/config/custom_components/jarvis_core_conversation/
```

Restart Home Assistant, add the Jarvis Core Conversation integration and provide
a Core endpoint reachable from Home Assistant.

## Updating

```bash
cd ~/jarvis
git fetch origin conversation-engine
git pull --ff-only origin conversation-engine
docker compose up -d --build
curl -fsS http://localhost:8000/health
```

Use `--ff-only`; do not force-reset a checkout containing uncommitted work.

## Version check

The authoritative source identity is stored in:

```text
bridge/app/version.py
```

See [PROJECT_STATUS.md](PROJECT_STATUS.md) before assuming features from an open
PR or historical release note are part of the current default branch.
