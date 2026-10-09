package com.aaron.jarvisvoice;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Insets;
import android.graphics.Typeface;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

/** Event and camera detail view; sensitive URLs and imagery never enter this client. */
public final class HomeDetailActivity extends Activity {
    public static final String EXTRA_KIND = "detail_kind";
    public static final String EXTRA_ID = "detail_id";
    public static final String EXTRA_STALE = "detail_stale";
    private LinearLayout root;
    private LinearLayout content;
    private JarvisAppShell.Header appShell;
    private boolean stale;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        String kind = getIntent().getStringExtra(EXTRA_KIND);
        String id = getIntent().getStringExtra(EXTRA_ID);
        stale = getIntent().getBooleanExtra(EXTRA_STALE, false);
        if (kind == null || id == null || id.isBlank()) { finish(); return; }
        setContentView(build());
        applyInsets();
        HomeSnapshotStore.Snapshot cached = new HomeSnapshotStore(
            this, new SecureStore(this).userId()
        ).load();
        if (cached == null) {
            renderMissing("Details are unavailable offline.");
            return;
        }
        try {
            HomeExperience home = HomeExperience.fromJson(cached.response());
            if ("lights".equals(kind)) {
                renderLights(home);
                return;
            }
            JSONObject item = "event".equals(kind)
                ? home.event(id)
                : "incident".equals(kind) ? home.incident(id) : home.camera(id);
            if (item == null) renderMissing("This detail is no longer in the current Home view.");
            else if ("event".equals(kind) || "incident".equals(kind)) renderEvent(item);
            else renderCamera(item);
        } catch (Exception exception) {
            renderMissing("Details could not be loaded.");
        }
    }

    private View build() {
        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(JarvisUi.WHITE);
        appShell = JarvisAppShell.create(
            this,
            JarvisAppShell.Destination.HOME,
            "Jarvis  ⌄",
            "Home details",
            new JarvisAppShell.Actions() {
                @Override public void onMode() { openChat(MainActivity.EXTRA_SHOW_MODE_PICKER); }
                @Override public void onNotifications() {
                    startActivity(new Intent(HomeDetailActivity.this, ProactiveActivity.class));
                }
                @Override public void onNewChat() { openChat(MainActivity.EXTRA_NEW_CHAT); }
                @Override public void onClearChat() { openChat(MainActivity.EXTRA_CONFIRM_CLEAR_CHAT); }
                @Override public void onSettings() {
                    startActivity(new Intent(HomeDetailActivity.this, SettingsActivity.class));
                }
                @Override public void onHome() { finish(); }
                @Override public void onChat() { openChat(null); }
                @Override public void onTasks() {
                    startActivity(new Intent(HomeDetailActivity.this, TasksActivity.class));
                    finish();
                }
            }
        );
        appShell.newChat.setVisibility(View.GONE);
        appShell.clearChat.setVisibility(View.GONE);
        root.addView(appShell.view, matchWrap());
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

    private void renderEvent(JSONObject event) {
        addTitle(event.optString("title", "Home activity"));
        addCard("What happened", event.optString("message", "Home activity was recorded."));
        String occurred = TasksActivity.relativeTime(
            event.optString("occurred_at", event.optString("last_seen"))
        );
        if (!occurred.isBlank()) addCard("When", occurred);
        addCard("Current status", natural(event.optString("status", "Recorded")));
        String why = event.optString("why", "");
        if (!why.isBlank()) addCard("Why Jarvis surfaced it", why);
        String area = event.optString("area_name", "");
        if (!area.isBlank()) addCard("Room", area);
        String device = event.optString("device_name", "");
        if (!device.isBlank()) addCard("Device", device);
        JSONArray evidence = event.optJSONArray("evidence_summary");
        if (evidence != null && evidence.length() > 0) {
            LinearLayout card = section("Evidence");
            for (int index = 0; index < evidence.length(); index++) {
                String item = evidence.optString(index, "");
                if (!item.isBlank()) card.addView(body(item), matchWrap(dp(7), 0));
            }
            content.addView(card, matchWrap(0, dp(12)));
        }
        String resolved = TasksActivity.relativeTime(event.optString("resolved_at", ""));
        if (event.optBoolean("recovery", false) || !resolved.isBlank()) {
            addCard(
                "Recovery",
                resolved.isBlank() ? "The condition recovered." : "Recovered " + resolved
            );
        }
        JSONArray actions = event.optJSONArray("actions");
        if (actions != null && actions.length() > 0) {
            LinearLayout card = section("Safe actions");
            for (int index = 0; index < actions.length(); index++) {
                String action = actions.optString(index, "");
                if (!action.isBlank()) card.addView(body(natural(action)), matchWrap(dp(7), 0));
            }
            content.addView(card, matchWrap(0, dp(12)));
        }
        addDiagnostics(event.optJSONObject("diagnostics"));
    }

    private void renderCamera(JSONObject camera) {
        addTitle(camera.optString("name", "Camera"));
        addCard("Availability", natural(camera.optString("availability", "Unknown")));
        String area = camera.optString("area_name", "");
        if (!area.isBlank()) addCard("Room", area);
        String activity = camera.optString("recent_activity", "");
        if (!activity.isBlank()) addCard("Recent activity", activity);
        String observed = TasksActivity.relativeTime(camera.optString("observed_at", ""));
        if (!observed.isBlank()) addCard("Observed", observed);
        addCard(
            "Live view",
            "Open this camera from Home Assistant for the existing authenticated live stream."
        );
        JSONObject diagnostic = new JSONObject();
        try { diagnostic.put("entity_id", camera.optString("entity_id")); }
        catch (Exception ignored) { }
        addDiagnostics(diagnostic);
    }

    private void renderLights(HomeExperience home) {
        addTitle("Lights");
        JSONArray rooms = home.rooms();
        boolean rendered = false;
        for (int roomIndex = 0; roomIndex < rooms.length(); roomIndex++) {
            JSONObject room = rooms.optJSONObject(roomIndex);
            if (room == null) continue;
            JSONArray lights = room.optJSONArray("lights");
            if (lights == null || lights.length() == 0) continue;
            LinearLayout card = section(room.optString("name", "Room"));
            for (int index = 0; index < lights.length(); index++) {
                JSONObject light = lights.optJSONObject(index);
                if (light == null) continue;
                LinearLayout row = new LinearLayout(this);
                row.setOrientation(LinearLayout.HORIZONTAL);
                TextView name = text(light.optString("name", "Light"), 14, JarvisUi.BLACK);
                name.setMaxLines(2);
                row.addView(name, new LinearLayout.LayoutParams(
                    0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f
                ));
                TextView state = body(natural(light.optString("state", "Unknown")));
                row.addView(state, new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT
                ));
                card.addView(row, matchWrap(dp(7), 0));
            }
            content.addView(card, matchWrap(0, dp(12)));
            rendered = true;
        }
        if (!rendered) addCard("Lights", "No grounded lights are available.");
    }

    private void renderMissing(String message) {
        addTitle("Home details");
        addCard("Unavailable", message);
    }

    private void addTitle(String value) {
        TextView title = text(value, 26, JarvisUi.BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        content.addView(title, matchWrap(0, dp(12)));
        if (stale) {
            TextView marker = body("Offline · Last known state");
            content.addView(marker, matchWrap(0, dp(12)));
        }
    }

    private void addCard(String heading, String value) {
        LinearLayout card = section(heading);
        card.addView(body(value), matchWrap(dp(7), 0));
        content.addView(card, matchWrap(0, dp(12)));
    }

    private void addDiagnostics(JSONObject diagnostic) {
        if (diagnostic == null || diagnostic.optString("entity_id", "").isBlank()) return;
        Button toggle = new Button(this);
        toggle.setAllCaps(false);
        toggle.setText("Diagnostics");
        toggle.setMinHeight(dp(JarvisUi.TOUCH_TARGET));
        LinearLayout details = section("Technical details");
        details.addView(body(diagnostic.optString("entity_id")), matchWrap(dp(7), 0));
        details.setVisibility(View.GONE);
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
        card.addView(title, matchWrap());
        return card;
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

    private TextView body(String value) { return text(value, 14, JarvisUi.MID); }
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
