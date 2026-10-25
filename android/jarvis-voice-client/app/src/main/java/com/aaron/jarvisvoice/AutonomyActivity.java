package com.aaron.jarvisvoice;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.graphics.Typeface;
import android.os.Bundle;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import org.json.JSONArray;
import org.json.JSONObject;

/** Plain-language, evidence-backed development dashboard. Never an authority toggle. */
public final class AutonomyActivity extends Activity {
    private LinearLayout content;
    private AutonomyClient client;
    private JSONObject state;
    private boolean busy;

    @Override protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        client = new AutonomyClient(new SecureStore(this));
        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(JarvisUi.WHITE);
        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        int padding = (int) (24 * getResources().getDisplayMetrics().density);
        content.setPadding(padding, padding * 2, padding, padding * 2);
        scroll.addView(content);
        setContentView(scroll);
    }
    @Override protected void onResume() { super.onResume(); refresh(); }
    @Override protected void onDestroy() { client.close(); super.onDestroy(); }

    private void refresh() {
        if (busy) return;
        busy = true;
        client.observe((value, error) -> {
            busy = false;
            if (error != null) { state = null; render(error); }
            else { state = value; render(null); }
        });
    }

    private void render(String error) {
        content.removeAllViews();
        button("Back", this::finish);
        heading("JARVIS DEVELOPMENT");
        button("Set up operator controls", this::enrollment);
        if (state == null) {
            heading("NEEDS ATTENTION"); text(error == null ? "Loading observations…" : error);
            button("Refresh", this::refresh); return;
        }
        String status = AutonomyPresentation.status(state);
        heading("AUTONOMOUS DEVELOPMENT " + status);
        text(state.optString("explanation"));
        if (!state.optString("observation_notice").isBlank()) text(state.optString("observation_notice"));
        heading("CURRENTLY WORKING ON");
        text(AutonomyPresentation.currentTitle(state));
        JSONObject current = state.optJSONObject("current");
        if (current == null) text("I'm watching for useful improvements.");
        else {
            heading("WHY"); text(current.optString("why"));
            heading("WHAT I'M DOING"); text(current.optString("doing"));
            text(AutonomyPresentation.progress(state));
        }
        heading("WHAT I'M PLANNING");
        JSONArray plans = state.optJSONArray("plans");
        if (plans == null || plans.length() == 0) text("No evidence-backed plans yet.");
        else for (int i = 0; i < plans.length(); i++) {
            JSONObject plan = plans.optJSONObject(i);
            if (plan != null) text("Considering · " + plan.optString("title"));
        }
        heading("RECENT IMPROVEMENTS");
        renderList(state.optJSONArray("recent_improvements"), "No autonomous improvements installed yet.");
        heading("EXPERIMENTS AND ROLLBACKS");
        renderList(state.optJSONArray("rollbacks"), "No rollback outcomes recorded.");
        heading("WHAT JARVIS CAN DO");
        JSONArray capabilities = state.optJSONArray("capabilities");
        for (String category : new String[]{"HOME", "VOICE", "COMMUNICATION", "RESEARCH", "DEVELOPER", "MEMORY", "AUTOMATION"}) {
            if (capabilities == null) break;
            boolean shown = false;
            for (int i = 0; i < capabilities.length(); i++) {
                JSONObject item = capabilities.optJSONObject(i);
                if (item == null || !item.optString("category").equals(category)) continue;
                if (!shown) { heading(category); shown = true; }
                text(item.optString("name") + " · " + AutonomyPresentation.capability(item));
            }
        }
        text("Not yet verified means this dashboard has no current evidence. It does not mean the capability is broken.");
        heading("TODAY"); text(state.optString("daily_summary", "No confirmed development activity."));
        button("STOP AUTONOMOUS DEVELOPMENT", () -> confirm("stop"));
        if (status.equals("STOPPED") && !AutonomyPresentation.resumeAvailable(state))
            text("Phase 1 observes and plans only. Activating autonomous development is not available in this release.");
        text("Operator key enrollment is required. A failed request never changes the displayed authority.");
        TextView details = text(state.optJSONObject("technical") == null ? "No details" : state.optJSONObject("technical").toString());
        details.setVisibility(View.GONE);
        button("Technical details", () -> details.setVisibility(details.getVisibility() == View.GONE ? View.VISIBLE : View.GONE));
        button("Refresh", this::refresh);
    }

    private void enrollment() {
        try {
            JSONObject request = AutonomyControl.enrollment();
            new AlertDialog.Builder(this).setTitle("Your phone's operator identity")
                .setMessage("Fingerprint:\n" + request.getString("key_id")
                    + "\n\nCompare this exact fingerprint during the local computer enrollment. "
                    + "Only the public request is copied. Your private key stays in Android Keystore. "
                    + "Copying this request does not enable controls or resume development.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Copy public request", (dialog, which) -> {
                    ClipboardManager clipboard = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
                    clipboard.setPrimaryClip(ClipData.newPlainText("Jarvis public operator enrollment", request.toString()));
                }).show();
        } catch (Exception unavailable) {
            new AlertDialog.Builder(this).setMessage("The phone's operator key is unavailable. Nothing was enrolled or enabled.")
                .setPositiveButton("OK", null).show();
        }
    }

    private void renderList(JSONArray items, String empty) {
        if (items == null || items.length() == 0) { text(empty); return; }
        for (int i = 0; i < items.length(); i++) {
            JSONObject item = items.optJSONObject(i);
            if (item != null) { text(item.optString("title")); text(item.optString("result")); }
        }
    }

    private void confirm(String action) {
        if (state == null || busy) return;
        boolean stop = action.equals("stop");
        new AlertDialog.Builder(this).setTitle(stop ? "Stop Jarvis improving himself?" : "Resume autonomous development?")
            .setMessage(stop ? "Jarvis will continue working normally, but automatic research, development and updates will stop."
                : "This requires your enrolled operator identity. Observe-only mode will not start engineering work.")
            .setNegativeButton("Cancel", null).setPositiveButton(stop ? "Stop" : "Resume", (dialog, which) -> {
                busy = true;
                client.control(action, state.optString("guardian_generation"), (result, error) -> {
                    busy = false;
                    if (error != null) new AlertDialog.Builder(this).setMessage(error).setPositiveButton("OK", null).show();
                    refresh(); // Never optimistically flip ACTIVE/STOPPED.
                });
            }).show();
    }

    private TextView text(String value) {
        TextView view = new TextView(this); view.setText(value); view.setTextSize(16);
        view.setTextColor(JarvisUi.BLACK); view.setPadding(0, 12, 0, 12); content.addView(view); return view;
    }
    private void heading(String value) { TextView view = text(value); view.setTypeface(null, Typeface.BOLD); }
    private void button(String value, Runnable action) {
        Button view = new Button(this); view.setText(value); view.setTextColor(JarvisUi.BLACK);
        view.setAllCaps(false);
        view.setMinHeight((int) (JarvisUi.TOUCH_TARGET * getResources().getDisplayMetrics().density));
        view.setBackground(JarvisUi.rounded(this, JarvisUi.SOFT, JarvisUi.RADIUS_PILL, 1, JarvisUi.LINE));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.setMargins(0, 12, 0, 12);
        view.setOnClickListener(v -> action.run()); content.addView(view, params);
    }
}
