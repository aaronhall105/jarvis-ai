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
                ? "Offline · Last updated " + sourceTime(home, receivedAt)
                : "Updated " + TasksActivity.relativeTimeMillis(receivedAt)
        );
        TextView title = text(room.optString("name", "Room"), 26, JarvisUi.BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        content.addView(title, matchWrap(0, dp(12)));

        LinearLayout occupancy = section("Occupancy");
        addValue(occupancy, room.optString("occupancy_summary", "Occupancy unknown"));
        JSONArray evidence = room.optJSONArray("occupancy_evidence");
        if (evidence != null) {
            for (int index = 0; index < evidence.length(); index++) {
                JSONObject item = evidence.optJSONObject(index);
                if (item != null) addPair(
                    occupancy,
                    item.optString("name", "Sensor"),
                    natural(item.optString("state", "unknown"))
                );
            }
        }
        content.addView(occupancy, matchWrap(0, dp(12)));
        addEntities("Lights", room.optJSONArray("lights"), "state", false, stale);
        addEntities("Cameras", room.optJSONArray("cameras"), "availability", true, stale);
        addEntities("Devices", room.optJSONArray("devices"), "availability", false, stale);
        addEntities("Appliances", room.optJSONArray("appliances"), "state_label", false, stale);
        addEntities("Media", room.optJSONArray("media"), "state", false, stale);
        addEntities("Climate", room.optJSONArray("climate"), "display_value", false, stale);
        addEvents(room.optJSONArray("recent_events"), stale);
        addAction(room.optJSONArray("quick_actions"), stale || !home.actionsAllowed());
        addDiagnostics(room);
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
            String state = item.optString(stateKey, item.optString("state", "Unknown"));
            LinearLayout row = pair(item.optString("name", heading), natural(state));
            if (camera) {
                String entityId = item.optString("entity_id");
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
                event.optString("title", "Home activity"),
                event.optString("message", "")
            );
            String eventId = event.optString("event_id");
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

    private void addAction(JSONArray actions, boolean disabled) {
        if (actions == null || actions.length() == 0) return;
        JSONObject action = actions.optJSONObject(0);
        if (action == null) return;
        Button button = new Button(this);
        button.setAllCaps(false);
        button.setText(action.optString("label", "Room action"));
        button.setMinHeight(dp(JarvisUi.TOUCH_TARGET));
        button.setBackground(JarvisUi.rounded(
            this, JarvisUi.SOFT, JarvisUi.RADIUS_PILL, 1, JarvisUi.LINE
        ));
        boolean enabled = !disabled && action.optBoolean("enabled", false);
        button.setEnabled(enabled);
        button.setAlpha(enabled ? 1f : 0.48f);
        String actionId = action.optString("action_id");
        button.setOnClickListener(view -> {
            button.setEnabled(false);
            button.setText("Working…");
            client.execute(actionId, new HomeClient.ActionCallback() {
                @Override public void onSuccess(JSONObject result) {
                    button.setText(
                        natural(result.optString("status", "UNKNOWN")) + " · "
                            + result.optString("message", "Action finished")
                    );
                }
                @Override public void onError(String message) {
                    button.setText("Failed · " + message);
                    button.setEnabled(true);
                }
            });
        });
        content.addView(button, matchWrap(0, dp(12)));
    }

    private void addDiagnostics(JSONObject room) {
        Button toggle = new Button(this);
        toggle.setAllCaps(false);
        toggle.setText("Diagnostics");
        toggle.setMinHeight(dp(JarvisUi.TOUCH_TARGET));
        LinearLayout details = section("Technical details");
        details.setVisibility(View.GONE);
        JSONArray groups = new JSONArray();
        groups.put(room.optJSONArray("lights"));
        groups.put(room.optJSONArray("cameras"));
        for (int group = 0; group < groups.length(); group++) {
            JSONArray values = groups.optJSONArray(group);
            if (values == null) continue;
            for (int index = 0; index < values.length(); index++) {
                JSONObject item = values.optJSONObject(index);
                if (item != null && !item.optString("entity_id").isBlank()) {
                    addValue(details, item.optString("entity_id"));
                }
            }
        }
        toggle.setOnClickListener(view -> details.setVisibility(
            details.getVisibility() == View.VISIBLE ? View.GONE : View.VISIBLE
        ));
        content.addView(toggle, matchWrap(0, dp(8)));
        content.addView(details, matchWrap(0, dp(12)));
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
