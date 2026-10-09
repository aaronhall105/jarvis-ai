package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import android.content.Context;
import android.content.Intent;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35)
public final class HomeExperienceUiTest {
    @Before public void setUp() {
        RuntimeEnvironment.getApplication()
            .getSharedPreferences(HomeSnapshotStore.PREFS, Context.MODE_PRIVATE)
            .edit().clear().commit();
        RuntimeEnvironment.getApplication()
            .getSharedPreferences("jarvis_voice_settings", Context.MODE_PRIVATE)
            .edit().clear().putString("realtime_user_name", "Aaron").commit();
    }

    @After public void tearDown() {
        RuntimeEnvironment.getApplication()
            .getSharedPreferences(HomeSnapshotStore.PREFS, Context.MODE_PRIVATE)
            .edit().clear().commit();
    }

    @Test public void sharedShellShowsHomeChatTasksAndHomeIsLauncher() throws Exception {
        HomeActivity activity = Robolectric.buildActivity(HomeActivity.class).create().get();
        View root = activity.findViewById(android.R.id.content);

        assertNotNull(findText(root, "J A R V I S"));
        assertNotNull(findText(root, "Home"));
        assertNotNull(findText(root, "Chat"));
        assertNotNull(findText(root, "Tasks"));
        assertTrue(findText(root, "Home").isSelected());

        Intent launcher = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
            .setPackage(RuntimeEnvironment.getApplication().getPackageName());
        android.content.pm.ResolveInfo resolved = RuntimeEnvironment.getApplication()
            .getPackageManager().resolveActivity(launcher, 0);
        assertNotNull(resolved);
        assertEquals(HomeActivity.class.getName(), resolved.activityInfo.name);
        activity.onDestroy();
    }

    @Test public void homeRendersGroundedUsefulSectionsWithoutRawEntityIds() throws Exception {
        HomeActivity activity = Robolectric.buildActivity(HomeActivity.class).create().get();
        render(activity, fixture(), false);
        View root = activity.findViewById(android.R.id.content);
        List<String> copy = flatten(root);

        assertTrue(copy.stream().anyMatch(value -> value.contains("1 thing needs attention")));
        assertNotNull(findText(root, "People"));
        assertNotNull(findText(root, "Rooms"));
        assertNotNull(findText(root, "Lights"));
        assertNotNull(findText(root, "Cameras"));
        assertNotNull(findText(root, "Devices"));
        assertNotNull(findText(root, "Appliances"));
        assertNotNull(findText(root, "Energy"));
        assertNotNull(findText(root, "Recent activity"));
        assertNull(findText(root, "Active media"));
        assertTrue(copy.stream().noneMatch(value -> value.contains("light.living_room_ceiling")));
        assertTrue(copy.stream().noneMatch(value -> value.contains("camera.living_room_clear")));
        activity.onDestroy();
    }

    @Test public void cachedHomeIsClearlyOfflineAndCannotRunActions() throws Exception {
        new HomeSnapshotStore(RuntimeEnvironment.getApplication(), "aaron")
            .save(fixture(), "etag", System.currentTimeMillis() - 90_000L);
        HomeActivity activity = Robolectric.buildActivity(HomeActivity.class).create().get();
        View root = activity.findViewById(android.R.id.content);

        assertTrue(flatten(root).stream().anyMatch(value -> value.contains("Offline · Updated")));
        assertNull(findText(root, "Last known state · Controls unavailable"));
        Button action = findButtonContaining(root, "Turn all displayed lights off");
        assertNotNull(action);
        assertFalse(action.isEnabled());
        activity.onDestroy();
    }

    @Test public void roomShowsUnknownInsteadOfClearForInactivePersonCamera() throws Exception {
        JSONObject value = fixture();
        value.getJSONArray("rooms").getJSONObject(0)
            .put("occupancy_state", "UNKNOWN")
            .put("occupancy_summary", "No current person detection")
            .put("occupancy_detail", "Occupancy remains unknown")
            .put("occupancy_evidence", new JSONArray().put(new JSONObject()
                .put("name", "Living Room Person")
                .put("state", "not_detected")));
        new HomeSnapshotStore(RuntimeEnvironment.getApplication(), "aaron")
            .save(value, "etag", System.currentTimeMillis());
        Intent intent = new Intent(RuntimeEnvironment.getApplication(), RoomDetailActivity.class)
            .putExtra(RoomDetailActivity.EXTRA_AREA_ID, "living_room");
        RoomDetailActivity activity = Robolectric.buildActivity(RoomDetailActivity.class, intent)
            .create().get();
        View root = activity.findViewById(android.R.id.content);

        assertNotNull(findText(root, "No current person detection"));
        assertNotNull(findText(root, "Occupancy remains unknown"));
        assertNull(findText(root, "Clear"));
        assertNull(findText(root, "Living Room Person"));
        assertNotNull(findText(root, "Living Room Ceiling"));
        assertNotNull(findText(root, "Living Room Camera"));
        assertNotNull(findText(root, "‹ Back"));
        activity.onDestroy();
    }

    @Test public void roomPlacesVerifiedLightActionInsideLightsBeforeCamera() throws Exception {
        new HomeSnapshotStore(RuntimeEnvironment.getApplication(), "aaron")
            .save(fixture(), "etag", System.currentTimeMillis());
        Intent intent = new Intent(RuntimeEnvironment.getApplication(), RoomDetailActivity.class)
            .putExtra(RoomDetailActivity.EXTRA_AREA_ID, "living_room");
        RoomDetailActivity activity = Robolectric.buildActivity(RoomDetailActivity.class, intent)
            .create().get();
        List<String> copy = flatten(activity.findViewById(android.R.id.content));

        int lights = copy.indexOf("Lights");
        int action = copy.indexOf("Turn all displayed lights off");
        int camera = copy.indexOf("Camera");
        assertTrue(lights >= 0 && action > lights && camera > action);
        activity.onDestroy();
    }

    @Test public void normalHomeNeverRendersSerializedNullWords() throws Exception {
        JSONObject value = fixture();
        value.getJSONArray("cameras").getJSONObject(0)
            .put("recent_activity", JSONObject.NULL)
            .put("area_name", JSONObject.NULL);
        value.getJSONObject("devices").getJSONArray("unavailable").getJSONObject(0)
            .put("area_name", JSONObject.NULL);
        HomeActivity activity = Robolectric.buildActivity(HomeActivity.class).create().get();
        render(activity, value, false);

        assertTrue(flatten(activity.findViewById(android.R.id.content)).stream().noneMatch(text -> {
            String lower = text.trim().toLowerCase(java.util.Locale.ROOT);
            return lower.equals("null") || lower.equals("none") || lower.equals("undefined")
                || lower.contains("· null");
        }));
        activity.onDestroy();
    }

    @Test public void eventDetailsUsePersistedWhyAndEvidence() throws Exception {
        new HomeSnapshotStore(RuntimeEnvironment.getApplication(), "aaron")
            .save(fixture(), "etag", System.currentTimeMillis());
        Intent intent = new Intent(RuntimeEnvironment.getApplication(), HomeDetailActivity.class)
            .putExtra(HomeDetailActivity.EXTRA_KIND, "event")
            .putExtra(HomeDetailActivity.EXTRA_ID, "event-1");
        var controller = Robolectric.buildActivity(HomeDetailActivity.class, intent).create();
        HomeDetailActivity activity = controller.get();
        View root = activity.findViewById(android.R.id.content);

        assertNotNull(findText(root, "Why Jarvis surfaced it"));
        assertNotNull(findText(root, "‹ Back"));
        assertNotNull(findText(root, "Validated running-to-finished transition."));
        assertNotNull(findText(root, "Washing machine changed from running to finished"));
        controller.destroy();
    }

    @Test public void lightsDetailGroupsGroundedLightsByRoom() throws Exception {
        new HomeSnapshotStore(RuntimeEnvironment.getApplication(), "aaron")
            .save(fixture(), "etag", System.currentTimeMillis());
        Intent intent = new Intent(RuntimeEnvironment.getApplication(), HomeDetailActivity.class)
            .putExtra(HomeDetailActivity.EXTRA_KIND, "lights")
            .putExtra(HomeDetailActivity.EXTRA_ID, "displayed");
        var controller = Robolectric.buildActivity(HomeDetailActivity.class, intent).create();
        View root = controller.get().findViewById(android.R.id.content);

        assertNotNull(findText(root, "Lights"));
        assertNotNull(findText(root, "Living Room"));
        assertNotNull(findText(root, "Living Room Ceiling"));
        assertNotNull(findText(root, "On"));
        assertNull(findText(root, "light.living_room_ceiling"));
        controller.destroy();
    }

    @Test public void offlineDetailRemainsClearlyMarkedLastKnown() throws Exception {
        new HomeSnapshotStore(RuntimeEnvironment.getApplication(), "aaron")
            .save(fixture(), "etag", System.currentTimeMillis() - 90_000L);
        Intent intent = new Intent(RuntimeEnvironment.getApplication(), HomeDetailActivity.class)
            .putExtra(HomeDetailActivity.EXTRA_KIND, "event")
            .putExtra(HomeDetailActivity.EXTRA_ID, "event-1")
            .putExtra(HomeDetailActivity.EXTRA_STALE, true);
        var controller = Robolectric.buildActivity(HomeDetailActivity.class, intent).create();

        assertNotNull(findText(
            controller.get().findViewById(android.R.id.content),
            "Offline · Last known state"
        ));
        controller.destroy();
    }

    @Test public void homeSnapshotCacheIsPrincipalIsolated() throws Exception {
        HomeSnapshotStore aaron = new HomeSnapshotStore(RuntimeEnvironment.getApplication(), "aaron");
        HomeSnapshotStore amber = new HomeSnapshotStore(RuntimeEnvironment.getApplication(), "amber");
        aaron.save(fixture(), "aaron-etag", 1_000L);

        assertNotNull(aaron.load());
        assertNull(amber.load());
    }

    @Test public void quickActionLabelsPreserveVerifiedPartialFailedAndUnknown() throws Exception {
        assertEquals(
            "Verified · 3 lights turned off.",
            HomeActivity.actionOutcomeLabel(new JSONObject()
                .put("status", "VERIFIED").put("message", "3 lights turned off."))
        );
        assertEquals(
            "Partial · 2 turned off. 1 failed or could not be confirmed.",
            HomeActivity.actionOutcomeLabel(new JSONObject()
                .put("status", "PARTIAL")
                .put("message", "2 turned off. 1 failed or could not be confirmed."))
        );
        assertTrue(HomeActivity.actionOutcomeLabel(new JSONObject()
            .put("status", "FAILED").put("message", "The action failed."))
            .startsWith("Failed ·"));
        assertTrue(HomeActivity.actionOutcomeLabel(new JSONObject()
            .put("status", "UNKNOWN").put("message", "The action could not be confirmed."))
            .startsWith("Unknown ·"));
    }

    @Test public void homeParseAndFirstRenderAreMeasured() throws Exception {
        JSONObject value = fixture();
        long parseStarted = System.nanoTime();
        HomeExperience home = HomeExperience.fromJson(value);
        double parseMs = (System.nanoTime() - parseStarted) / 1_000_000.0;

        HomeActivity activity = Robolectric.buildActivity(HomeActivity.class).create().get();
        Method render = HomeActivity.class.getDeclaredMethod(
            "render", HomeExperience.class, boolean.class, long.class
        );
        render.setAccessible(true);
        long renderStarted = System.nanoTime();
        render.invoke(activity, home, false, System.currentTimeMillis());
        double renderMs = (System.nanoTime() - renderStarted) / 1_000_000.0;

        System.out.printf(
            java.util.Locale.ROOT,
            "alpha38_home_parse_ms=%.3f alpha38_home_first_render_ms=%.3f%n",
            parseMs,
            renderMs
        );
        assertNotNull(findText(activity.findViewById(android.R.id.content), "People"));
        activity.onDestroy();
    }

    private static void render(HomeActivity activity, JSONObject value, boolean stale)
        throws Exception {
        Method render = HomeActivity.class.getDeclaredMethod(
            "render", HomeExperience.class, boolean.class, long.class
        );
        render.setAccessible(true);
        render.invoke(
            activity,
            HomeExperience.fromJson(value),
            stale,
            System.currentTimeMillis()
        );
    }

    private static JSONObject fixture() throws Exception {
        String actionId = "lights-off:displayed:revision-1";
        JSONObject light = new JSONObject()
            .put("entity_id", "light.living_room_ceiling")
            .put("name", "Living Room Ceiling")
            .put("state", "on")
            .put("available", true)
            .put("area_id", "living_room")
            .put("area_name", "Living Room");
        JSONObject camera = new JSONObject()
            .put("entity_id", "camera.living_room_clear")
            .put("name", "Living Room Camera")
            .put("area_id", "living_room")
            .put("area_name", "Living Room")
            .put("availability", "ONLINE")
            .put("recent_activity", "Person detected")
            .put("person_status", "DETECTED")
            .put("motion_status", "NOT_DETECTED");
        JSONObject action = new JSONObject()
            .put("action_id", actionId)
            .put("kind", "TURN_OFF_EXACT_LIGHT_SET")
            .put("label", "Turn all displayed lights off")
            .put("enabled", true)
            .put("target_count", 1)
            .put("target_entity_ids", new JSONArray().put("light.living_room_ceiling"));
        JSONObject event = new JSONObject()
            .put("event_id", "event-1")
            .put("title", "Washing machine finished")
            .put("message", "The washing machine has finished.")
            .put("status", "NOTIFIED")
            .put("occurred_at", "2026-10-08T11:52:00Z")
            .put("why", "Validated running-to-finished transition.")
            .put("evidence_summary", new JSONArray()
                .put("Washing machine changed from running to finished"));
        JSONObject room = new JSONObject()
            .put("area_id", "living_room")
            .put("name", "Living Room")
            .put("occupancy_state", "OCCUPIED")
            .put("occupancy_summary", "Person detected")
            .put("occupancy_detail", "Grounded person-detection evidence")
            .put("occupancy_evidence", new JSONArray())
            .put("lights_on_count", 1)
            .put("lights_total", 1)
            .put("lights", new JSONArray().put(light))
            .put("cameras", new JSONArray().put(camera))
            .put("devices", new JSONArray())
            .put("appliances", new JSONArray())
            .put("climate", new JSONArray())
            .put("media", new JSONArray())
            .put("recent_events", new JSONArray().put(event))
            .put("quick_actions", new JSONArray().put(action))
            .put("diagnostics", new JSONObject()
                .put("occupancy_evidence", new JSONArray())
                .put("raw_entities", new JSONArray())
                .put("camera_sources", new JSONArray())
                .put("media_control_paths", new JSONArray()));
        return new JSONObject()
            .put("schema_version", 1)
            .put("generated_at", "2026-10-08T12:00:00Z")
            .put("revision", "revision-1")
            .put("source_freshness", new JSONObject()
                .put("status", "LIVE")
                .put("actions_allowed", true)
                .put("observed_at", "2026-10-08T12:00:00Z"))
            .put("overall_status", new JSONObject()
                .put("status", "ATTENTION")
                .put("headline", "1 thing needs attention")
                .put("detail", "Hallway Camera unavailable · Aaron is home · 1 light on"))
            .put("people", new JSONArray()
                .put(new JSONObject().put("name", "Aaron").put("presence", "HOME"))
                .put(new JSONObject().put("name", "Amber").put("presence", "AWAY")))
            .put("rooms", new JSONArray().put(room))
            .put("lights", new JSONObject()
                .put("on_count", 1).put("total_count", 1)
                .put("items", new JSONArray().put(light)))
            .put("cameras", new JSONArray().put(camera))
            .put("devices", new JSONObject()
                .put("unavailable_count", 1)
                .put("partial_count", 0)
                .put("unavailable", new JSONArray().put(new JSONObject()
                    .put("name", "Hallway Camera")
                    .put("area_name", "Hallway"))))
            .put("appliances", new JSONArray().put(new JSONObject()
                .put("name", "Washing Machine").put("state_label", "Running")))
            .put("energy", new JSONArray().put(new JSONObject()
                .put("name", "House Power").put("display_value", "742 W")))
            .put("active_media", new JSONArray())
            .put("current_incidents", new JSONArray())
            .put("recent_events", new JSONArray().put(event))
            .put("quick_actions", new JSONArray().put(action));
    }

    private static TextView findText(View view, String text) {
        if (view instanceof TextView item && text.contentEquals(item.getText())) return item;
        if (view instanceof ViewGroup group) {
            for (int index = 0; index < group.getChildCount(); index++) {
                TextView found = findText(group.getChildAt(index), text);
                if (found != null) return found;
            }
        }
        return null;
    }

    private static Button findButtonContaining(View view, String text) {
        if (view instanceof Button item && item.getText().toString().contains(text)) return item;
        if (view instanceof ViewGroup group) {
            for (int index = 0; index < group.getChildCount(); index++) {
                Button found = findButtonContaining(group.getChildAt(index), text);
                if (found != null) return found;
            }
        }
        return null;
    }

    private static List<String> flatten(View view) {
        List<String> values = new ArrayList<>();
        if (view instanceof TextView item) values.add(item.getText().toString());
        if (view instanceof ViewGroup group) {
            for (int index = 0; index < group.getChildCount(); index++) {
                values.addAll(flatten(group.getChildAt(index)));
            }
        }
        return values;
    }
}
