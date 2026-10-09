package com.aaron.jarvisvoice;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Insets;
import android.graphics.Typeface;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.text.TextUtils;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

/** Primary grounded whole-home destination backed only by Core HomeExperience. */
public final class HomeActivity extends Activity {
    private static final String PERFORMANCE_TAG = "JarvisHomePerf";
    private static final long FOREGROUND_REFRESH_MS = 20_000L;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable refresher = this::refresh;
    private HomeClient client;
    private SecureStore store;
    private LinearLayout root;
    private LinearLayout content;
    private JarvisAppShell.Header appShell;
    private TextView freshness;
    private Button retry;
    private boolean visible;
    private int generation;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        store = new SecureStore(this);
        client = new HomeClient(this);
        configureWindow();
        setContentView(build());
        applySystemBarAppearance();
        applySystemInsets();
        renderCached();
    }

    @Override protected void onResume() {
        super.onResume();
        visible = true;
        refresh();
    }

    @Override protected void onPause() {
        visible = false;
        handler.removeCallbacks(refresher);
        super.onPause();
    }

    @Override protected void onDestroy() {
        client.close();
        super.onDestroy();
    }

    private View build() {
        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(JarvisUi.WHITE);
        appShell = JarvisAppShell.create(
            this,
            JarvisAppShell.Destination.HOME,
            DeveloperRoutingPolicy.routesToDeveloper(store.assistantMode())
                ? "Developer  ⌄" : "Jarvis  ⌄",
            "Home intelligence",
            shellActions()
        );
        appShell.newChat.setVisibility(View.GONE);
        appShell.clearChat.setVisibility(View.GONE);
        root.addView(appShell.view, matchWrap());

        LinearLayout statusRow = new LinearLayout(this);
        statusRow.setOrientation(LinearLayout.HORIZONTAL);
        statusRow.setGravity(Gravity.CENTER_VERTICAL);
        statusRow.setPadding(dp(JarvisUi.PAGE_MARGIN), 0, dp(JarvisUi.PAGE_MARGIN), dp(8));
        freshness = text("Loading home…", 13, JarvisUi.MID);
        statusRow.addView(freshness, new LinearLayout.LayoutParams(
            0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f
        ));
        retry = button("Retry");
        retry.setVisibility(View.GONE);
        retry.setOnClickListener(view -> refresh());
        statusRow.addView(retry, wrapWrap());
        root.addView(statusRow, matchWrap());

        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(
            dp(JarvisUi.PAGE_MARGIN), 0, dp(JarvisUi.PAGE_MARGIN), dp(JarvisUi.SPACE_32)
        );
        scroll.addView(content, new ScrollView.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        root.addView(scroll, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f
        ));
        return root;
    }

    private JarvisAppShell.Actions shellActions() {
        return new JarvisAppShell.Actions() {
            @Override public void onMode() { openChat(MainActivity.EXTRA_SHOW_MODE_PICKER); }
            @Override public void onNotifications() {
                startActivity(new Intent(HomeActivity.this, ProactiveActivity.class));
            }
            @Override public void onNewChat() { openChat(MainActivity.EXTRA_NEW_CHAT); }
            @Override public void onClearChat() { openChat(MainActivity.EXTRA_CONFIRM_CLEAR_CHAT); }
            @Override public void onSettings() {
                startActivity(new Intent(HomeActivity.this, SettingsActivity.class));
            }
            @Override public void onHome() { }
            @Override public void onChat() { openChat(null); }
            @Override public void onTasks() {
                startActivity(new Intent(HomeActivity.this, TasksActivity.class)
                    .addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT));
                finish();
                overridePendingTransition(0, 0);
            }
        };
    }

    private void openChat(String extra) {
        Intent intent = new Intent(this, MainActivity.class)
            .addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT);
        if (extra != null) intent.putExtra(extra, true);
        startActivity(intent);
        finish();
        overridePendingTransition(0, 0);
    }

    private void refresh() {
        handler.removeCallbacks(refresher);
        int requestGeneration = ++generation;
        client.fetch(new HomeClient.HomeCallback() {
            @Override public void onSuccess(
                HomeExperience home, boolean fromCache, long receivedAtMillis
            ) {
                if (requestGeneration != generation) return;
                retry.setVisibility(View.GONE);
                boolean degraded = fromCache || !"LIVE".equals(home.freshnessStatus());
                render(home, degraded, receivedAtMillis);
                if (visible) handler.postDelayed(refresher, FOREGROUND_REFRESH_MS);
            }

            @Override public void onError(String message) {
                if (requestGeneration != generation) return;
                retry.setVisibility(View.VISIBLE);
                HomeSnapshotStore.Snapshot cached = client.cached();
                if (cached != null) {
                    try {
                        render(
                            HomeExperience.fromJson(cached.response()),
                            true,
                            cached.receivedAtMillis()
                        );
                    } catch (Exception ignored) {
                        renderUnavailable(message);
                    }
                } else {
                    renderUnavailable(message);
                }
                if (visible) handler.postDelayed(refresher, 40_000L);
            }
        });
    }

    private void renderCached() {
        HomeSnapshotStore.Snapshot cached = client.cached();
        if (cached == null) return;
        try {
            render(HomeExperience.fromJson(cached.response()), true, cached.receivedAtMillis());
        } catch (Exception ignored) { }
    }

    private void renderUnavailable(String message) {
        freshness.setText("Offline");
        content.removeAllViews();
        LinearLayout card = card();
        card.addView(title("Home state unavailable"), matchWrap());
        card.addView(body(message), matchWrap(dp(6), 0));
        content.addView(card, matchWrap(0, dp(12)));
    }

    private void render(HomeExperience home, boolean stale, long receivedAtMillis) {
        long started = SystemClock.elapsedRealtimeNanos();
        content.removeAllViews();
        freshness.setText(
            stale
                ? "Offline · Last updated " + sourceTime(home, receivedAtMillis)
                : "Updated " + TasksActivity.relativeTimeMillis(receivedAtMillis)
        );
        content.addView(summaryCard(home, stale), matchWrap(0, dp(14)));
        if (home.people().length() > 0) addPeople(home.people());
        if (home.rooms().length() > 0) addRooms(home.rooms());
        if (home.lights().optInt("total_count", 0) > 0) addLights(home, stale);
        if (home.cameras().length() > 0) addCameras(home.cameras(), stale);
        JSONObject devices = home.devices();
        if (devices.optInt("unavailable_count", 0) + devices.optInt("partial_count", 0) > 0) {
            addDevices(devices);
        }
        if (home.appliances().length() > 0) addSimpleSection("Appliances", home.appliances());
        if (home.energy().length() > 0) addEnergy(home.energy());
        if (home.activeMedia().length() > 0) addSimpleSection("Active media", home.activeMedia());
        if (home.incidents().length() + home.events().length() > 0) addActivity(home, stale);
        double elapsedMs = (SystemClock.elapsedRealtimeNanos() - started) / 1_000_000.0;
        Log.d(PERFORMANCE_TAG, String.format(java.util.Locale.ROOT, "render_ms=%.3f", elapsedMs));
    }

    private View summaryCard(HomeExperience home, boolean stale) {
        LinearLayout card = card();
        TextView headline = text(home.headline(), 22, JarvisUi.BLACK);
        headline.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        headline.setMaxLines(4);
        card.addView(headline, matchWrap());
        if (stale) {
            TextView marker = text("Last known state · Controls unavailable", 13, JarvisUi.MID);
            marker.setPadding(0, dp(8), 0, 0);
            card.addView(marker, matchWrap());
        }
        return card;
    }

    private void addPeople(JSONArray people) {
        LinearLayout card = section("People");
        for (int index = 0; index < people.length(); index++) {
            JSONObject person = people.optJSONObject(index);
            if (person == null) continue;
            addLabelValue(
                card,
                person.optString("name", "Person"),
                naturalState(person.optString("presence", "UNKNOWN"))
            );
        }
        content.addView(card, matchWrap(0, dp(12)));
    }

    private void addRooms(JSONArray rooms) {
        LinearLayout card = section("Rooms");
        for (int index = 0; index < rooms.length(); index++) {
            JSONObject room = rooms.optJSONObject(index);
            if (room == null) continue;
            String areaId = room.optString("area_id");
            String detail = room.optString("occupancy_summary", "Occupancy unknown");
            int lightsOn = room.optInt("lights_on_count", 0);
            int cameraCount = room.optJSONArray("cameras") == null
                ? 0 : room.optJSONArray("cameras").length();
            if (lightsOn > 0) detail += " · " + lightsOn + " light" + (lightsOn == 1 ? " on" : "s on");
            if (cameraCount > 0) detail += " · " + cameraCount + " camera" + (cameraCount == 1 ? "" : "s");
            View row = navigationRow(room.optString("name", "Room"), detail);
            row.setOnClickListener(view -> startActivity(
                new Intent(this, RoomDetailActivity.class)
                    .putExtra(RoomDetailActivity.EXTRA_AREA_ID, areaId)
            ));
            card.addView(row, matchWrap(dp(4), 0));
        }
        content.addView(card, matchWrap(0, dp(12)));
    }

    private void addLights(HomeExperience home, boolean stale) {
        JSONObject lights = home.lights();
        int on = lights.optInt("on_count", 0);
        int total = lights.optInt("total_count", 0);
        LinearLayout card = section("Lights");
        View list = navigationRow(
            on == 0 ? "All lights are off" : on + (on == 1 ? " light on" : " lights on"),
            total + (total == 1 ? " light" : " lights")
        );
        list.setOnClickListener(view -> startActivity(
            new Intent(this, HomeDetailActivity.class)
                .putExtra(HomeDetailActivity.EXTRA_KIND, "lights")
                .putExtra(HomeDetailActivity.EXTRA_ID, "displayed")
                .putExtra(HomeDetailActivity.EXTRA_STALE, stale)
        ));
        card.addView(list, matchWrap(dp(4), 0));
        JSONObject action = firstAction(home.quickActions(), "TURN_OFF_EXACT_LIGHT_SET");
        if (action != null && on > 0) {
            Button control = button(action.optString("label", "Turn displayed lights off"));
            boolean enabled = !stale && home.actionsAllowed() && action.optBoolean("enabled", false);
            control.setEnabled(enabled);
            control.setAlpha(enabled ? 1f : 0.48f);
            control.setContentDescription(
                enabled ? control.getText() : control.getText() + ", unavailable while offline"
            );
            control.setOnClickListener(view -> executeAction(control, action.optString("action_id")));
            card.addView(control, matchWrap(dp(10), 0));
        }
        content.addView(card, matchWrap(0, dp(12)));
    }

    private void executeAction(Button button, String actionId) {
        if (actionId.isBlank()) return;
        button.setEnabled(false);
        button.setText("Working…");
        client.execute(actionId, new HomeClient.ActionCallback() {
            @Override public void onSuccess(JSONObject result) {
                button.setText(actionOutcomeLabel(result));
                handler.postDelayed(HomeActivity.this::refresh, 800L);
            }
            @Override public void onError(String message) {
                button.setText("Failed · " + message);
                button.setEnabled(true);
            }
        });
    }

    private void addCameras(JSONArray cameras, boolean stale) {
        LinearLayout card = section("Cameras");
        for (int index = 0; index < cameras.length(); index++) {
            JSONObject camera = cameras.optJSONObject(index);
            if (camera == null) continue;
            String detail = naturalState(camera.optString("availability", "UNKNOWN"));
            String activity = camera.optString("recent_activity", "");
            if (!activity.isBlank()) detail += " · " + activity;
            View row = navigationRow(camera.optString("name", "Camera"), detail);
            String entityId = camera.optString("entity_id");
            row.setOnClickListener(view -> startActivity(
                new Intent(this, HomeDetailActivity.class)
                    .putExtra(HomeDetailActivity.EXTRA_KIND, "camera")
                    .putExtra(HomeDetailActivity.EXTRA_ID, entityId)
                    .putExtra(HomeDetailActivity.EXTRA_STALE, stale)
            ));
            card.addView(row, matchWrap(dp(4), 0));
        }
        content.addView(card, matchWrap(0, dp(12)));
    }

    private void addDevices(JSONObject devices) {
        LinearLayout card = section("Devices");
        int unavailable = devices.optInt("unavailable_count", 0);
        int partial = devices.optInt("partial_count", 0);
        if (unavailable > 0) addLabelValue(
            card,
            unavailable + (unavailable == 1 ? " device unavailable" : " devices unavailable"),
            "Needs attention"
        );
        JSONArray values = devices.optJSONArray("unavailable");
        if (values != null) {
            for (int index = 0; index < values.length(); index++) {
                JSONObject device = values.optJSONObject(index);
                if (device != null) addLabelValue(
                    card,
                    device.optString("name", "Device"),
                    device.optString("area_name", "Unavailable")
                );
            }
        }
        if (partial > 0) addLabelValue(
            card,
            partial + (partial == 1 ? " device partly available" : " devices partly available"),
            "Some diagnostic entities are unavailable"
        );
        content.addView(card, matchWrap(0, dp(12)));
    }

    private void addSimpleSection(String heading, JSONArray values) {
        LinearLayout card = section(heading);
        for (int index = 0; index < values.length(); index++) {
            JSONObject item = values.optJSONObject(index);
            if (item == null) continue;
            String state = item.optString("state_label", item.optString("state", "Active"));
            addLabelValue(card, item.optString("name", heading), naturalState(state));
        }
        content.addView(card, matchWrap(0, dp(12)));
    }

    private void addEnergy(JSONArray energy) {
        LinearLayout card = section("Energy");
        for (int index = 0; index < energy.length(); index++) {
            JSONObject item = energy.optJSONObject(index);
            if (item == null) continue;
            addLabelValue(
                card,
                item.optString("name", "Energy"),
                item.optString("display_value", item.optString("value", "Unknown"))
            );
        }
        content.addView(card, matchWrap(0, dp(12)));
    }

    private void addActivity(HomeExperience home, boolean stale) {
        LinearLayout card = section("Recent activity");
        JSONArray incidents = home.incidents();
        for (int index = 0; index < incidents.length(); index++) {
            JSONObject item = incidents.optJSONObject(index);
            if (item == null) continue;
            View row = navigationRow(
                item.optString("title", "Needs attention"),
                item.optString("message", naturalState(item.optString("status", "ACTIVE")))
            );
            String incidentId = item.optString("incident_id");
            row.setOnClickListener(view -> startActivity(
                new Intent(this, HomeDetailActivity.class)
                    .putExtra(HomeDetailActivity.EXTRA_KIND, "incident")
                    .putExtra(HomeDetailActivity.EXTRA_ID, incidentId)
                    .putExtra(HomeDetailActivity.EXTRA_STALE, stale)
            ));
            card.addView(row, matchWrap(dp(4), 0));
        }
        JSONArray events = home.events();
        for (int index = 0; index < events.length(); index++) {
            JSONObject event = events.optJSONObject(index);
            if (event == null) continue;
            View row = navigationRow(
                event.optString("title", "Home activity"),
                event.optString("message", "")
            );
            String eventId = event.optString("event_id");
            row.setOnClickListener(view -> startActivity(
                new Intent(this, HomeDetailActivity.class)
                    .putExtra(HomeDetailActivity.EXTRA_KIND, "event")
                    .putExtra(HomeDetailActivity.EXTRA_ID, eventId)
                    .putExtra(HomeDetailActivity.EXTRA_STALE, stale)
            ));
            card.addView(row, matchWrap(dp(4), 0));
        }
        content.addView(card, matchWrap(0, dp(12)));
    }

    private LinearLayout section(String heading) {
        LinearLayout card = card();
        TextView title = text(heading, 16, JarvisUi.BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        card.addView(title, matchWrap(0, dp(4)));
        return card;
    }

    private LinearLayout card() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(16), dp(15), dp(16), dp(15));
        card.setBackground(JarvisUi.rounded(
            this, JarvisUi.WHITE, JarvisUi.RADIUS_MEDIUM, 1, JarvisUi.LINE
        ));
        card.setElevation(0f);
        card.setStateListAnimator(null);
        return card;
    }

    private void addLabelValue(LinearLayout parent, String label, String value) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        TextView name = text(label, 15, JarvisUi.BLACK);
        name.setMaxLines(2);
        name.setEllipsize(TextUtils.TruncateAt.END);
        row.addView(name, new LinearLayout.LayoutParams(
            0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f
        ));
        TextView state = text(value, 13, JarvisUi.MID);
        state.setGravity(Gravity.END);
        state.setMaxLines(2);
        row.addView(state, new LinearLayout.LayoutParams(
            0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f
        ));
        parent.addView(row, matchWrap(dp(8), 0));
    }

    private View navigationRow(String label, String detail) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setMinimumHeight(dp(JarvisUi.TOUCH_TARGET));
        row.setClickable(true);
        row.setFocusable(true);
        LinearLayout copy = new LinearLayout(this);
        copy.setOrientation(LinearLayout.VERTICAL);
        TextView name = text(label, 15, JarvisUi.BLACK);
        name.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        name.setMaxLines(2);
        name.setEllipsize(TextUtils.TruncateAt.END);
        copy.addView(name, matchWrap());
        if (!detail.isBlank()) {
            TextView sub = text(detail, 13, JarvisUi.MID);
            sub.setMaxLines(2);
            sub.setEllipsize(TextUtils.TruncateAt.END);
            copy.addView(sub, matchWrap(dp(2), 0));
        }
        row.addView(copy, new LinearLayout.LayoutParams(
            0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f
        ));
        row.addView(text("›", 24, JarvisUi.MID), wrapWrap());
        row.setContentDescription(label + (detail.isBlank() ? "" : ", " + detail));
        return row;
    }

    private static JSONObject firstAction(JSONArray actions, String kind) {
        for (int index = 0; index < actions.length(); index++) {
            JSONObject action = actions.optJSONObject(index);
            if (action != null && kind.equals(action.optString("kind"))) return action;
        }
        return null;
    }

    static String actionOutcomeLabel(JSONObject result) {
        String status = naturalState(result == null ? "UNKNOWN" : result.optString(
            "status", "UNKNOWN"
        ));
        String message = result == null ? "Action outcome unknown" : result.optString(
            "message", "Action finished"
        );
        return status + " · " + message;
    }

    private static String sourceTime(HomeExperience home, long fallbackMillis) {
        String observedAt = home.observedAt();
        if (observedAt != null && !observedAt.isBlank()) {
            String relative = TasksActivity.relativeTime(observedAt);
            if (!relative.isBlank()) return relative;
        }
        return TasksActivity.relativeTimeMillis(fallbackMillis);
    }

    private static String naturalState(String value) {
        if (value == null || value.isBlank()) return "Unknown";
        String lower = value.replace('_', ' ').toLowerCase(java.util.Locale.ROOT);
        return Character.toUpperCase(lower.charAt(0)) + lower.substring(1);
    }

    private TextView title(String value) {
        TextView title = text(value, 20, JarvisUi.BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        return title;
    }

    private TextView body(String value) { return text(value, 14, JarvisUi.MID); }
    private TextView text(String value, float size, int color) {
        return JarvisUi.text(this, value, size, color);
    }

    private Button button(String label) {
        Button button = new Button(this);
        button.setAllCaps(false);
        button.setText(label);
        button.setTextSize(13);
        button.setTextColor(JarvisUi.BLACK);
        button.setMinHeight(dp(JarvisUi.TOUCH_TARGET));
        button.setPadding(dp(14), dp(7), dp(14), dp(7));
        button.setBackground(JarvisUi.rounded(
            this, JarvisUi.SOFT, JarvisUi.RADIUS_PILL, 1, JarvisUi.LINE
        ));
        return button;
    }

    private LinearLayout.LayoutParams matchWrap() { return matchWrap(0, 0); }
    private LinearLayout.LayoutParams matchWrap(int top, int bottom) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        );
        params.setMargins(0, top, 0, bottom);
        return params;
    }
    private LinearLayout.LayoutParams wrapWrap() {
        return new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT
        );
    }
    private int dp(int value) { return JarvisUi.dp(this, value); }

    private void configureWindow() {
        Window window = getWindow();
        window.setStatusBarColor(Color.TRANSPARENT);
        window.setNavigationBarColor(Color.TRANSPARENT);
        window.setNavigationBarDividerColor(Color.TRANSPARENT);
        window.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
    }

    private void applySystemBarAppearance() {
        View decorView = getWindow().getDecorView();
        decorView.post(() -> {
            WindowInsetsController controller = decorView.getWindowInsetsController();
            if (controller == null) return;
            int appearance = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
            controller.setSystemBarsAppearance(appearance, appearance);
        });
    }

    private void applySystemInsets() {
        root.setOnApplyWindowInsetsListener((view, windowInsets) -> {
            Insets bars = windowInsets.getInsets(WindowInsets.Type.systemBars());
            appShell.applySystemInsets(bars);
            root.setPadding(0, 0, 0, bars.bottom);
            return windowInsets;
        });
        root.requestApplyInsets();
    }
}
