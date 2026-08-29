# Android alpha17 experience reconciliation

The authoritative phone-app experience is the Android tree at commit
`3279a57ac8593cc2eba70d1678350dea25235a96` (`19.0.0-alpha17`). The unified
Core remains authoritative for every server-side component. This is a source
reconciliation, not a visual recreation.

## File decisions

| Area | Decision | Authoritative result |
| --- | --- | --- |
| `ChatHistoryActivity` | A — keep exactly from alpha17 | The alpha17 history navigation and presentation remain unchanged. |
| `ImprovementsActivity` | A — keep exactly from alpha17 | The alpha17 Improvements experience remains unchanged. |
| `ProactiveActivity` / House activity | A — keep exactly from alpha17 | The alpha17 House activity entry and activity remain unchanged. |
| `MainActivity` | B — keep alpha17 UI, port new logic | Restores the alpha17 logo, `Jarvis` title, top-bar order, `More options` popup, normal composer, delete confirmation and Jarvis message-action popup. Current Developer routing is added to the existing menu and is hidden from the normal composer. |
| `SettingsActivity` | B — keep alpha17 UI, port new logic | Restores alpha17 section headers/cards and platform spinners; adds Integrations and Developer as matching alpha17-style sections. |
| `ChatHistoryStore` | B — keep alpha17 UX, port new logic | Keeps the current conversation-aware schema, migration and deletion behavior so alpha21 data remains readable. |
| `AndroidManifest.xml` | B — keep app identity, port declarations | Keeps `com.aaron.jarvisvoice` and current assistant, overlay, wake, Wear bridge, updater, Integrations activity and OAuth deep-link declarations. |
| alpha17 phone branding and launcher resources | A — exact alpha17 bytes | Restores the exact source resources and removes the later phone-only replacement branding. |
| `SecureStore` | C — current functional implementation | Preserves all current encrypted preferences, tokens, routes, Developer state and integration-token handling. |
| `JarvisRealtimeClient`, `RealtimeAudioEngine`, `RealtimeProtocol` | C — current functional implementation | Preserves client turn IDs, response fencing, reconnection, failover, interruption and recovery. |
| `VoiceService`, `StandardSpeechEngine` | C — current functional implementation | Preserves Standard/Live modes, barge-in, audio ownership, durable delivery and conversation deletion. |
| `CoreEndpointSelector`, `EndpointRoutePolicy`, `NetworkQualityMonitor` | C — current functional implementation | Preserves local/remote route selection and network-transition recovery. |
| `UpdateManager` and update screens | C — current functional implementation | Preserves the current signed OTA and rollback behavior. |
| assistant, overlay and wake components | B/C — alpha17 presentation with current implementation | Restores the alpha17 overlay logo/title while preserving current default-assistant, compact-overlay and wake-word behavior. |
| Wear bridge, Wear app and `wearprotocol` | C — current functional implementation | Preserves phone/watch transport, routing, Tile and watch assistant support. |
| `IntegrationsActivity`, `IntegrationsClient`, `IntegrationProvider` | D — current files required | Adds current account state, connect/reconnect/disconnect and truthful Setup Required rendering. |
| Developer/Codex policies and client | D — current files required | Adds current authenticated Developer operation without replacing the alpha17 Jarvis chat screen. |
| turn recovery, delivery fencing and client-turn stores | D — current files required | Adds current durable realtime compatibility behind the alpha17 UI. |

## Exact alpha17 resource evidence

These phone resources must remain byte-identical to commit `3279a57`:

| Resource | SHA-256 |
| --- | --- |
| `drawable-nodpi/jarvis_logo_ui.png` | `0ca048af7edd74991c4509f798699d606e2053d1a052e8b2ea8d629925ce4e03` |
| `drawable-nodpi/ic_launcher_foreground.png` | `ae551628ee4e5f1c7645d4c9a4099cc1ba2b462fe110479d2b9be9ae7ce0165a` |
| `drawable/ic_jarvis.xml` | `943f998c9aedb91a0c3d55a6e5ce3ac77f0899660591da13919de7df29693ec2` |
| `drawable/ic_jarvis_status.xml` | `0ec41d1566b6416544d580ad47ed2c1f92d712df0773587de26dd94710a022be` |

Release verification also asserts rendered alpha17 navigation through
Robolectric and compiled APK bytecode/resources. It fails if the normal Jarvis
screen loses House activity, New chat, More options, Chat history,
Improvements, Delete current chat, Settings, the alpha17 composer, or the
alpha17 logo while also requiring current Integrations, Developer, realtime,
assistant, wake and Wear markers.
