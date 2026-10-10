# Alpha39 Home Assistant dashboard and occupancy output

Alpha39 does not overwrite the live Lovelace database. The repository contains
the production dashboard source at `docs/configuration/jarvis_alpha38_dashboard.yaml`.
It renders the same authenticated Core `HomeExperience` used by Android and
conversation; only camera streams and explicit controls remain native HA cards.

## Install or update the integration

1. Back up the Home Assistant configuration directory and the current dashboard
   before changing anything.
2. From the extracted release package, run
   `tools/install_jarvis_home_v1_8_0.sh <HA_CONFIG>`. The installer requires an
   existing Jarvis integration, backs it up under
   `<HA_CONFIG>/backups/jarvis-home-v1.8.0/<timestamp>`, validates the package,
   restores the backup on installation failure, and runs `ha core check` when
   the HA CLI is available.
3. Restart Home Assistant and open **Settings → Devices & services → Jarvis Core
   Conversation → Configure**.
4. Enter the same `JARVIS_MOBILE_VOICE_TOKEN` configured on Core in **Jarvis
   mobile token (for Home status)**. Do not place the token in dashboard YAML.
5. Confirm that `sensor.jarvis_home_status`, `sensor.jarvis_lights_on`,
   `sensor.jarvis_unavailable_devices`, and `sensor.jarvis_living_room` are
available before installing the dashboard.

The room sensor state is the Core-owned automation contract: `occupied`,
`likely_occupied`, `probably_clear`, or `unknown`. Its attributes retain
confidence, freshness, source health, last strong evidence, and evidence count.
Never interpret `unknown` as clear.

## Install the dashboard without replacing an existing one

1. Open **Settings → Dashboards → Add dashboard** and create a new dashboard
   titled `Jarvis` with URL `lovelace-jarvis`.
2. Open that dashboard, choose **Edit dashboard → Raw configuration editor**,
   and paste the contents of `jarvis_alpha38_dashboard.yaml`.
3. Save, then verify Home, Rooms, Living Room, Cameras, Energy, People, and More.
4. Confirm the existing `button-card` and `card-mod` resources load. The file
   adds no new frontend dependency.
5. Confirm both Reolink camera entities still open their native HA more-info/live
   view and that `light.living_room_ceiling` remains controllable.

If the generated room sensor entity ID differs because of an existing entity
registry entry, replace only the corresponding `sensor.jarvis_<area>` reference
in the YAML. Do not rewrite counts or occupancy with HA templates; those
semantics remain owned by Core.

Rollback is non-destructive: remove the separate `lovelace-jarvis` dashboard or
restore its backed-up raw configuration. Removing it does not alter devices,
automations, the existing default dashboard, or Jarvis Core state.

The integration installer intentionally does not edit `.storage/lovelace*`,
`ui-lovelace.yaml`, or any existing dashboard. Dashboard installation remains a
separate, visible user action after the integration and its sensors are healthy.

The package also carries
`alpha39_living_room_occupancy_automation.yaml`. Both automations are disabled
by default and are not copied into the live HA configuration. Review the local
entity IDs and the documented manual-override limitation before explicitly
importing and enabling them.
