package com.aaron.jarvisvoice;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Insets;
import android.graphics.Typeface;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

/** Grounded room detail rendered from the shared Core projection. */
public final class RoomDetailActivity extends Activity {
    public static final String EXTRA_AREA_ID = "area_id";
    private String areaId;
    private HomeClient client;
    private LinearLayout root;
    private LinearLayout content;
    private TextView freshness;
    private JarvisAppShell.Header appShell;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        areaId = getIntent().getStringExtra(EXTRA_AREA_ID);
        if (areaId == null || areaId.isBlank()) { finish(); return; }
        client = new HomeClient(this);
        setContentView(build());
        applyInsets();
        renderCached();
    }

    @Override protected void onResume() {
        super.onResume();
        client.fetch(new HomeClient.HomeCallback() {
            @Override public void onSuccess(
                HomeExperience home, boolean fromCache, long receivedAtMillis
            ) {
                render(home, fromCache || !"LIVE".equals(home.freshnessStatus()), receivedAtMillis);
            }
            @Override public void onError(String message) {
                HomeSnapshotStore.Snapshot cached = client.cached();
                if (cached == null) { freshness.setText("Offline"); return; }
                try {
                    render(
                        HomeExperience.fromJson(cached.response()), true, cached.receivedAtMillis()
                    );
                } catch (Exception ignored) { freshness.setText("Offline"); }
            }
        });
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
            "Jarvis  ⌄",
            "Room details",
            new JarvisAppShell.Actions() {
                @Override public void onMode() { openChat(MainActivity.EXTRA_SHOW_MODE_PICKER); }
                @Override public void onNotifications() {
                    startActivity(new Intent(RoomDetailActivity.this, ProactiveActivity.class));
                }
                @Override public void onNewChat() { openChat(MainActivity.EXTRA_NEW_CHAT); }
                @Override public void onClearChat() { openChat(MainActivity.EXTRA_CONFIRM_CLEAR_CHAT); }
                @Override public void onSettings() {
                    startActivity(new Intent(RoomDetailActivity.this, SettingsActivity.class));
                }
                @Override public void onHome() { finish(); }
                @Override public void onChat() { openChat(null); }
                @Override public void onTasks() {
                    startActivity(new Intent(RoomDetailActivity.this, TasksActivity.class));
                    finish();
                }
            }
        );
        appShell.newChat.setVisibility(View.GONE);
        appShell.clearChat.setVisibility(View.GONE);
        root.addView(appShell.view, matchWrap());
        Button back = pillButton("‹ Back");
        back.setContentDescription("Back to Home");
        back.setOnClickListener(view -> finish());
        LinearLayout backRow = new LinearLayout(this);
        backRow.setPadding(dp(16), 0, dp(16), dp(6));
        backRow.addView(back, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        root.addView(backRow, matchWrap());
        freshness = text("Loading room…", 13, JarvisUi.MID);
        freshness.setPadding(dp(16), 0, dp(16), dp(8));
        root.addView(freshness, matchWrap());
        ScrollView scroll = new ScrollView(this);
        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(dp(16), 0, dp(16), dp(32));
        scroll.addView(content, new ScrollView.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        root.addView(scroll, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f
        ));
        return root;
    }

    private void renderCached() {
        HomeSnapshotStore.Snapshot cached = client.cached();
        if (cached == null) return;
        try {
            render(HomeExperience.fromJson(cached.response()), true, cached.receivedAtMillis());
        } catch (Exception ignored) { }
    }

    private void render(HomeExperience home, boolean stale, long receivedAt) {
        JSONObject room = home.room(areaId);
        content.removeAllViews();
        if (room == null) {
            freshness.setText("Room unavailable");
            return;
        }
        freshness.setText(
            stale
                ? "Offline · Updated " + sourceTime(home, receivedAt)
                : "Updated " + TasksActivity.relativeTimeMillis(receivedAt)
        );
        TextView title = text(safe(room, "name", "Room"), 26, JarvisUi.BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        content.addView(title, matchWrap(0, dp(12)));

        LinearLayout occupancy = section("Occupancy");
        addValue(occupancy, safe(room, "occupancy_summary", "Occupancy unknown"));
        String occupancyDetail = safe(room, "occupancy_detail", "");
        if (!occupancyDetail.isBlank()) addValue(occupancy, occupancyDetail);
        content.addView(occupancy, matchWrap(0, dp(12)));
        addLights(room, stale || !home.actionsAllowed());
        addCameras(room.optJSONArray("cameras"), stale);
        addEntities("Appliances", room.optJSONArray("appliances"), "state_label", false, stale);
        addEntities("Media", room.optJSONArray("media"), "state", false, stale);
        addEntities("Climate", room.optJSONArray("climate"), "display_value", false, stale);
        addDeviceSummary(room.optJSONArray("devices"));
        addEvents(room.optJSONArray("recent_events"), stale);
        addDiagnostics(room);
    }

    private void addLights(JSONObject room, boolean disabled) {
        JSONArray values = room.optJSONArray("lights");
        if (values == null || values.length() == 0) return;
        LinearLayout card = section("Lights");
        for (int index = 0; index < values.length(); index++) {
            JSONObject item = values.optJSONObject(index);
            if (item != null) addPair(
                card,
                safe(item, "name", "Light"),
                natural(safe(item, "state", "unknown"))
            );
        }
        JSONArray actions = room.optJSONArray("quick_actions");
        JSONObject action = actions == null ? null : actions.optJSONObject(0);
        if (action != null) card.addView(actionButton(action, disabled), matchWrap(dp(10), 0));
        content.addView(card, matchWrap(0, dp(12)));
    }

    private void addCameras(JSONArray values, boolean stale) {
        if (values == null || values.length() == 0) return;
        LinearLayout card = section("Camera");
        for (int index = 0; index < values.length(); index++) {
            JSONObject item = values.optJSONObject(index);
            if (item == null) continue;
            LinearLayout row = pair(
                safe(item, "name", "Camera"),
                natural(safe(item, "availability", "unknown"))
            );
            String entityId = safe(item, "entity_id", "");
            row.setClickable(true);
            row.setFocusable(true);
            row.setMinimumHeight(dp(JarvisUi.TOUCH_TARGET));
            row.setOnClickListener(view -> startActivity(
                new Intent(this, HomeDetailActivity.class)
                    .putExtra(HomeDetailActivity.EXTRA_KIND, "camera")
                    .putExtra(HomeDetailActivity.EXTRA_ID, entityId)
                    .putExtra(HomeDetailActivity.EXTRA_STALE, stale)
            ));
            card.addView(row, matchWrap(dp(7), 0));
            String person = natural(safe(item, "person_status", "UNKNOWN"));
            String motion = natural(safe(item, "motion_status", "UNKNOWN"));
            addValue(card, "Person: " + person + " · Motion: " + motion);
        }
        content.addView(card, matchWrap(0, dp(12)));
    }

    private void addDeviceSummary(JSONArray devices) {
        if (devices == null || devices.length() == 0) return;
        int available = 0;
        int unavailable = 0;
        int partial = 0;
        for (int index = 0; index < devices.length(); index++) {
            JSONObject device = devices.optJSONObject(index);
            String state = safe(device, "availability", "UNKNOWN");
            if ("UNAVAILABLE".equals(state)) unavailable++;
            else if ("PARTIAL".equals(state)) partial++;
            else available++;
        }
        LinearLayout card = section("Devices");
        addValue(card, available + " available"
            + (unavailable > 0 ? " · " + unavailable + " unavailable" : "")
            + (partial > 0 ? " · " + partial + " partial" : ""));
        content.addView(card, matchWrap(0, dp(12)));
    }

    private void addEntities(
        String heading,
        JSONArray values,
        String stateKey,
        boolean camera,
        boolean stale
    ) {
        if (values == null || values.length() == 0) return;
        LinearLayout card = section(heading);
        for (int index = 0; index < values.length(); index++) {
            JSONObject item = values.optJSONObject(index);
            if (item == null) continue;
            String state = safe(item, stateKey, safe(item, "state", "Unknown"));
            LinearLayout row = pair(safe(item, "name", heading), natural(state));
            if (camera) {
                String entityId = safe(item, "entity_id", "");
                row.setClickable(true);
                row.setFocusable(true);
                row.setMinimumHeight(dp(JarvisUi.TOUCH_TARGET));
                row.setOnClickListener(view -> startActivity(
                    new Intent(this, HomeDetailActivity.class)
                        .putExtra(HomeDetailActivity.EXTRA_KIND, "camera")
                        .putExtra(HomeDetailActivity.EXTRA_ID, entityId)
                        .putExtra(HomeDetailActivity.EXTRA_STALE, stale)
                ));
            }
            card.addView(row, matchWrap(dp(7), 0));
        }
        content.addView(card, matchWrap(0, dp(12)));
    }

    private void addEvents(JSONArray events, boolean stale) {
        if (events == null || events.length() == 0) return;
        LinearLayout card = section("Recent activity");
        for (int index = 0; index < events.length(); index++) {
            JSONObject event = events.optJSONObject(index);
            if (event == null) continue;
            LinearLayout row = pair(
                safe(event, "title", "Home activity"),
                safe(event, "message", "")
            );
            String eventId = safe(event, "event_id", "");
            row.setClickable(true);
            row.setFocusable(true);
            row.setMinimumHeight(dp(JarvisUi.TOUCH_TARGET));
            row.setOnClickListener(view -> startActivity(
                new Intent(this, HomeDetailActivity.class)
                    .putExtra(HomeDetailActivity.EXTRA_KIND, "event")
                    .putExtra(HomeDetailActivity.EXTRA_ID, eventId)
                    .putExtra(HomeDetailActivity.EXTRA_STALE, stale)
            ));
            card.addView(row, matchWrap(dp(7), 0));
        }
        content.addView(card, matchWrap(0, dp(12)));
    }

    private Button actionButton(JSONObject action, boolean disabled) {
        Button button = pillButton(safe(action, "label", "Room action"));
        boolean enabled = !disabled && action.optBoolean("enabled", false);
        button.setEnabled(enabled);
        button.setAlpha(enabled ? 1f : 0.48f);
        String actionId = safe(action, "action_id", "");
        button.setOnClickListener(view -> {
            button.setEnabled(false);
            button.setText("Working…");
            client.execute(actionId, new HomeClient.ActionCallback() {
                @Override public void onSuccess(JSONObject result) {
                    button.setText(
                        natural(safe(result, "status", "UNKNOWN")) + " · "
                            + safe(result, "message", "Action finished")
                    );
                }
                @Override public void onError(String message) {
                    button.setText("Failed · " + message);
                    button.setEnabled(true);
                }
            });
        });
        return button;
    }

    private void addDiagnostics(JSONObject room) {
        Button toggle = pillButton("Diagnostics");
        LinearLayout details = section("Technical details");
        details.setVisibility(View.GONE);
        JSONObject diagnostics = room.optJSONObject("diagnostics");
        addDiagnosticGroup(details, "Occupancy evidence", diagnostics, "occupancy_evidence");
        addDiagnosticGroup(details, "Raw entities", diagnostics, "raw_entities");
        addDiagnosticGroup(details, "Camera sources", diagnostics, "camera_sources");
        addDiagnosticGroup(details, "Light sources", diagnostics, "light_sources");
        addDiagnosticGroup(details, "Control paths", diagnostics, "media_control_paths");
        toggle.setOnClickListener(view -> details.setVisibility(
            details.getVisibility() == View.VISIBLE ? View.GONE : View.VISIBLE
        ));
        content.addView(toggle, matchWrap(0, dp(8)));
        content.addView(details, matchWrap(0, dp(12)));
    }

    private void addDiagnosticGroup(
        LinearLayout details, String heading, JSONObject diagnostics, String key
    ) {
        if (diagnostics == null) return;
        JSONArray values = diagnostics.optJSONArray(key);
        if (values == null || values.length() == 0) return;
        addValue(details, heading);
        for (int index = 0; index < values.length(); index++) {
            JSONObject item = values.optJSONObject(index);
            if (item == null) continue;
            String value = safe(item, "entity_id", safe(item, "name", ""));
            if (value.isBlank()) value = safe(item, "primary_entity_id", "");
            if (!value.isBlank()) addValue(details, value);
            addDiagnosticIds(details, item.optJSONArray("alternate_entity_ids"));
            addDiagnosticIds(details, item.optJSONArray("diagnostic_entity_ids"));
        }
    }

    private void addDiagnosticIds(LinearLayout details, JSONArray values) {
        if (values == null) return;
        for (int index = 0; index < values.length(); index++) {
            String value = safe(values, index);
            if (!value.isBlank()) addValue(details, value);
        }
    }

    private LinearLayout section(String heading) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(16), dp(14), dp(16), dp(14));
        card.setBackground(JarvisUi.rounded(
            this, JarvisUi.WHITE, JarvisUi.RADIUS_MEDIUM, 1, JarvisUi.LINE
        ));
        TextView title = text(heading, 16, JarvisUi.BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        card.addView(title, matchWrap(0, dp(4)));
        return card;
    }

    private void addValue(LinearLayout parent, String value) {
        parent.addView(text(value, 14, JarvisUi.MID), matchWrap(dp(7), 0));
    }

    private void addPair(LinearLayout parent, String label, String value) {
        parent.addView(pair(label, value), matchWrap(dp(7), 0));
    }

    private LinearLayout pair(String label, String value) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.addView(text(label, 14, JarvisUi.BLACK), new LinearLayout.LayoutParams(
            0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f
        ));
        TextView state = text(value, 13, JarvisUi.MID);
        state.setGravity(Gravity.END);
        row.addView(state, new LinearLayout.LayoutParams(
            0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f
        ));
        return row;
    }

    private void openChat(String extra) {
        Intent intent = new Intent(this, MainActivity.class);
        if (extra != null) intent.putExtra(extra, true);
        startActivity(intent);
        finish();
    }

    private static String natural(String value) {
        if (value == null || value.isBlank()) return "Unknown";
        String lower = value.replace('_', ' ').toLowerCase(java.util.Locale.ROOT);
        return Character.toUpperCase(lower.charAt(0)) + lower.substring(1);
    }

    private static String safe(JSONObject value, String key, String fallback) {
        return HomeExperience.text(value, key, fallback);
    }

    private static String safe(JSONArray values, int index) {
        String value = values.optString(index, "").trim();
        String lower = value.toLowerCase(java.util.Locale.ROOT);
        return "null".equals(lower) || "none".equals(lower) || "undefined".equals(lower)
            ? "" : value;
    }

    private Button pillButton(String label) {
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

    private static String sourceTime(HomeExperience home, long fallbackMillis) {
        String observedAt = home.observedAt();
        if (observedAt != null && !observedAt.isBlank()) {
            String relative = TasksActivity.relativeTime(observedAt);
            if (!relative.isBlank()) return relative;
        }
        return TasksActivity.relativeTimeMillis(fallbackMillis);
    }

    private TextView text(String value, float size, int color) {
        return JarvisUi.text(this, value, size, color);
    }
    private LinearLayout.LayoutParams matchWrap() { return matchWrap(0, 0); }
    private LinearLayout.LayoutParams matchWrap(int top, int bottom) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        );
        params.setMargins(0, top, 0, bottom);
        return params;
    }
    private int dp(int value) { return JarvisUi.dp(this, value); }

    private void applyInsets() {
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
            appShell.applySystemInsets(bars);
            root.setPadding(0, 0, 0, bars.bottom);
            return insets;
        });
        root.requestApplyInsets();
    }
}
