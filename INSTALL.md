# Installing Jarvis

These instructions cover the current unified prerelease, `v19.0.0-alpha27`,
from the sole long-lived branch `jarvis/unified-production`. Core application
version is `3.7.0`; realtime protocol version is `2`.

Production data, credentials, Android application data, and Watch settings must
be preserved during updates.

## Requirements

- A Linux host with Git, Docker Engine, and Docker Compose
- A supported model provider configured on the Core host
- A trusted LAN, VPN, or authenticated reverse proxy for client access
- An Android Phone for the Phone client
- A Wear OS device for the Watch client, when used
- Optional Home Assistant, Google, Microsoft, Frigate, or other provider
  configuration for the corresponding capabilities

Java 17, Android SDK 36, and Gradle 9.4.1 are required only when building the
Phone or Watch projects locally.

## 1. Clone the authoritative branch

```bash
git clone --branch jarvis/unified-production \
  https://github.com/aaronhall105/jarvis-ai.git
cd jarvis-ai
```

Do not deploy production from historical tags, archive tags, backup directories,
or another checkout.

## 2. Configure Jarvis Core

Create a private environment file:

```bash
cp .env.example .env
chmod 600 .env
```

Set only the values needed by the deployment. At minimum, configure the model
provider and authenticated client access. Home Assistant, integrations,
realtime voice, vision, and proactive work are optional and remain unavailable
or Setup Required until configured.

Never commit `.env`. Never place access tokens, refresh tokens, OAuth client
secrets, encryption keys, Home Assistant credentials, or signing material in
documentation, source, Android resources, or public release assets.

The standard Compose configuration persists:

```text
./config -> /app/config
./data   -> /app/data
./logs   -> /app/logs
```

`data/` contains conversations, memory, durable jobs, receipts, integration
accounts, realtime recovery state, and other databases. Back it up before
maintenance and never recreate it during a normal source deployment.

## 3. Start or update Core

For a fresh local deployment:

```bash
docker compose up -d --build
docker compose ps
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/health/ready
```

For the established production layout, use the repository's guarded deployment
workflow:

```bash
./tools/deploy_unified_core.sh --verify-only
./tools/deploy_unified_core.sh
```

The guarded workflow requires a clean `jarvis/unified-production` worktree at
the exact remote head, validates the product baseline, checks persistent
databases before and after replacement, preserves known mounts, and verifies
the deployed source SHA.

Do not expose an unauthenticated Core directly to the public internet.

## 4. Install or update the Phone client

Download the production-signed Phone APK and its checksum from the
[v19.0.0-alpha27 release](https://github.com/aaronhall105/jarvis-ai/releases/tag/v19.0.0-alpha27).
The package is `com.aaron.jarvisvoice`.

Install it over the existing Jarvis application. Do not uninstall Jarvis or
clear application data to perform an update; doing so can remove conversations,
settings, encrypted client credentials, assistant configuration, and wake-word
preferences.

After installation:

1. Configure the trusted LAN and optional remote Core endpoints.
2. Store the mobile credential through Jarvis Settings; use the same securely
   configured `JARVIS_MOBILE_VOICE_TOKEN` on Core.
3. Grant microphone and notification permissions as needed.
4. Select Jarvis as the default digital assistant if assistant-button support
   is desired.
5. Review battery policy when wake-word or background operation is enabled.
6. Confirm Diagnostics, realtime synchronization, and provider states.

The in-app updater reads only validated release-hosted channel manifests. See
[Android OTA releases](docs/ANDROID_OTA_RELEASES.md).

## 5. Install or update the Wear OS client

The same release contains the production-signed Watch APK. Install it in place
on the Watch without clearing data. Phone and Watch must come from the same
product release so their shared protocol and source provenance match.

Validate the Watch activity, Tile, microphone, assistant role, audio route, and
Phone/Watch conversation continuity on the target device. See
[Wear endpoint](docs/wear-endpoint-v1.md).

## 6. Home Assistant

Copy the integration directory into Home Assistant:

```text
home_assistant/custom_components/jarvis_core_conversation/
    → /config/custom_components/jarvis_core_conversation/
```

Restart Home Assistant, add the Jarvis Core Conversation integration, and
configure the reachable authenticated Core endpoint. Home Assistant remains
authoritative for entity, device, area, automation, and camera state.

Verify reads before writes. A successful write must be grounded against a real
entity, executed through policy, verified against provider evidence, and
recorded as an action receipt.

## 7. Optional integrations

Integrations & Accounts is configured on Core and opened from the existing
Phone app. Google setup requires a Google Cloud project, enabled APIs, an OAuth
consent screen, an OAuth Web application client, an exact HTTPS Core callback,
and securely hosted secrets. Users authorize in the system browser; they do not
give their Google password to Jarvis.

Follow [Integrations & Accounts](docs/INTEGRATIONS_ACCOUNTS_V1.md) for minimum
scopes and secure configuration. Google, Gmail, Calendar, Contacts, and
Microsoft must continue to show Setup Required until their credentials and
provider health are valid.

## 8. Developer gateway

The Developer/Codex gateway is optional and must be installed from the same
authoritative revision as Core:

```bash
./developer_gateway/install.sh
curl -fsS http://127.0.0.1:8765/health
```

Keep its credential outside Git and expose it only through the approved
authenticated network path. See [Developer mode](docs/developer-mode.md).

## Updating source safely

```bash
git fetch origin --prune
git switch jarvis/unified-production
git pull --ff-only origin jarvis/unified-production
```

Do not force-reset a checkout containing uncommitted work. Validate source,
database integrity, runtime health, and exact provenance before and after a
production deployment.
