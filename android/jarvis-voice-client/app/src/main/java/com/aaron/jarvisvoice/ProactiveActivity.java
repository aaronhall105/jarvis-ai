package com.aaron.jarvisvoice;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Insets;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.ImageButton;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.SeekBar;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import java.text.DateFormat;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

public final class ProactiveActivity extends Activity {
    private static final int BLACK = Color.rgb(20, 20, 20);
    private static final int MID = Color.rgb(103, 103, 103);
    private static final int LINE = Color.rgb(226, 226, 226);
    private static final int SOFT = Color.rgb(246, 246, 246);
    private static final int WHITE = Color.WHITE;

    private ProactiveClient client;
    private SecureStore store;
    private LinearLayout feed;
    private TextView status;
    private TextView importanceValue;
    private Switch enabled;
    private Switch notify;
    private Switch speak;
    private SeekBar importance;
    private final Map<String, CheckBox> categoryChecks = new LinkedHashMap<>();
    private ProactiveSettings settings;
    private List<ProactiveEvent> events = new ArrayList<>();

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        client = new ProactiveClient(this);
        store = new SecureStore(this);
        setContentView(build());
        load();
    }

    @Override protected void onResume() {
        super.onResume();
        load();
    }

    @Override protected void onDestroy() {
        client.close();
        super.onDestroy();
    }

    private View build() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(WHITE);

        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);

        ImageButton back = iconButton(R.drawable.ic_back, "Back");
        back.setOnClickListener(view -> finish());
        top.addView(back, new LinearLayout.LayoutParams(dp(42), dp(42)));

        LinearLayout titleBlock = new LinearLayout(this);
        titleBlock.setOrientation(LinearLayout.VERTICAL);
        titleBlock.setPadding(dp(12), 0, 0, 0);
        TextView title = text("Jarvis activity", 23, BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        titleBlock.addView(title, wrap());
        status = text("Connecting to Jarvis Core", 12, MID);
        titleBlock.addView(status, wrap());
        top.addView(
            titleBlock,
            new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        );

        Button refresh = secondaryButton("Refresh");
        refresh.setOnClickListener(view -> load());
        top.addView(
            refresh,
            new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(44))
        );
        root.addView(top, matchWrap());

        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(dp(16), dp(6), dp(16), dp(30));

        content.addView(section("Proactive settings"), matchWrap(8, 8));
        content.addView(settingsCard(), matchWrap(0, 18));
        content.addView(section("Recent activity"), matchWrap(0, 8));
        feed = new LinearLayout(this);
        feed.setOrientation(LinearLayout.VERTICAL);
        content.addView(feed, matchWrap());

        scroll.addView(
            content,
            new ScrollView.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        );
        root.addView(
            scroll,
            new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f)
        );

        root.setOnApplyWindowInsetsListener((view, insets) -> {
            Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
            top.setPadding(
                dp(10) + bars.left,
                dp(8) + bars.top,
                dp(12) + bars.right,
                dp(8)
            );
            scroll.setPadding(bars.left, 0, bars.right, bars.bottom);
            return insets;
        });
        root.requestApplyInsets();
        return root;
    }

    private LinearLayout settingsCard() {
        LinearLayout card = card();
        enabled = new Switch(this);
        card.addView(toggleRow(
            "Proactive intelligence",
            "Record useful household events.",
            enabled
        ));
        notify = new Switch(this);
        card.addView(toggleRow(
            "Phone notifications",
            "Notify this profile through Home Assistant.",
            notify
        ));
        speak = new Switch(this);
        card.addView(toggleRow(
            "Spoken announcements",
            "Requires an Assist Satellite entity on Core.",
            speak
        ));

        LinearLayout scoreRow = new LinearLayout(this);
        scoreRow.setOrientation(LinearLayout.HORIZONTAL);
        scoreRow.setGravity(Gravity.CENTER_VERTICAL);
        scoreRow.addView(
            text("Minimum importance", 15, BLACK),
            new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        );
        importanceValue = text("80", 14, MID);
        scoreRow.addView(importanceValue, wrap());
        card.addView(scoreRow, matchWrap(10, 0));

        importance = new SeekBar(this);
        importance.setMax(100);
        importance.setProgress(80);
        importance.setOnSeekBarChangeListener(
            new SeekBar.OnSeekBarChangeListener() {
                @Override public void onProgressChanged(
                    SeekBar seekBar,
                    int progress,
                    boolean fromUser
                ) {
                    importanceValue.setText(Integer.toString(progress));
                }
                @Override public void onStartTrackingTouch(SeekBar seekBar) {}
                @Override public void onStopTrackingTouch(SeekBar seekBar) {}
            }
        );
        card.addView(importance, matchWrap());
        card.addView(
            text(
                "Quiet hours: 22:00–07:00. Critical security events may still notify.",
                12,
                MID
            ),
            matchWrap(0, 8)
        );

        for (String category : ProactiveSettings.CATEGORIES) {
            CheckBox check = new CheckBox(this);
            check.setText(label(category));
            check.setTextSize(14);
            check.setTextColor(BLACK);
            check.setChecked(true);
            categoryChecks.put(category, check);
            card.addView(check, matchWrap());
        }

        Button save = primaryButton("Save proactive settings");
        save.setOnClickListener(view -> save());
        card.addView(save, matchWrap(10, 0));
        return card;
    }

    private void load() {
        status.setText("Loading Jarvis activity");
        client.feed(new ProactiveClient.FeedCallback() {
            @Override public void onSuccess(
                List<ProactiveEvent> loaded,
                ProactiveSettings loadedSettings
            ) {
                events = loaded;
                settings = loadedSettings;
                applySettings();
                renderFeed();
                status.setText(events.size() + " recent events · " + store.userName());
            }

            @Override public void onError(String message) {
                status.setText("Jarvis Core unavailable");
                feed.removeAllViews();
                TextView error = text(message, 14, MID);
                error.setGravity(Gravity.CENTER);
                error.setPadding(0, dp(30), 0, dp(30));
                feed.addView(error, matchWrap());
            }
        });
    }

    private void applySettings() {
        enabled.setChecked(settings.enabled);
        notify.setChecked(settings.notifyEnabled);
        speak.setChecked(settings.speakEnabled);
        importance.setProgress(settings.minImportance);
        importanceValue.setText(Integer.toString(settings.minImportance));
        for (Map.Entry<String, CheckBox> entry : categoryChecks.entrySet()) {
            entry.getValue().setChecked(
                settings.categories.getOrDefault(entry.getKey(), true)
            );
        }
    }

    private void save() {
        if (settings == null) return;
        settings.enabled = enabled.isChecked();
        settings.notifyEnabled = notify.isChecked();
        settings.speakEnabled = speak.isChecked();
        settings.minImportance = importance.getProgress();
        for (Map.Entry<String, CheckBox> entry : categoryChecks.entrySet()) {
            settings.categories.put(entry.getKey(), entry.getValue().isChecked());
        }
        status.setText("Saving settings");
        client.save(settings, new ProactiveClient.ResultCallback() {
            @Override public void onSuccess() {
                status.setText("Proactive settings saved");
                load();
            }
            @Override public void onError(String message) {
                status.setText("Unable to save settings");
                Toast.makeText(
                    ProactiveActivity.this,
                    message,
                    Toast.LENGTH_LONG
                ).show();
            }
        });
    }

    private void renderFeed() {
        feed.removeAllViews();
        if (events.isEmpty()) {
            TextView empty = text(
                "No proactive activity yet. Jarvis will add useful events here as the house changes.",
                14,
                MID
            );
            empty.setGravity(Gravity.CENTER);
            empty.setPadding(0, dp(34), 0, dp(34));
            feed.addView(empty, matchWrap());
            return;
        }
        for (ProactiveEvent event : events) {
            feed.addView(eventCard(event), matchWrap(0, 10));
        }
    }

    private View eventCard(ProactiveEvent event) {
        LinearLayout card = card();
        LinearLayout titleRow = new LinearLayout(this);
        titleRow.setOrientation(LinearLayout.HORIZONTAL);
        titleRow.setGravity(Gravity.CENTER_VERTICAL);

        TextView title = text(event.title, 16, BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        titleRow.addView(
            title,
            new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        );
        TextView score = text(
            ProactiveUiPolicy.importanceLabel(event.importance)
                + " " + event.importance,
            11,
            MID
        );
        score.setPadding(dp(8), dp(4), dp(8), dp(4));
        score.setBackground(rounded(SOFT, 10, 0, Color.TRANSPARENT));
        titleRow.addView(score, wrap());
        card.addView(titleRow, matchWrap());

        TextView message = text(event.message, 15, BLACK);
        message.setPadding(0, dp(8), 0, 0);
        card.addView(message, matchWrap());
        TextView reason = text("Why: " + event.reason, 12, MID);
        reason.setPadding(0, dp(6), 0, 0);
        card.addView(reason, matchWrap());
        TextView source = text(
            event.entityId + " · "
                + DateFormat.getDateTimeInstance(
                    DateFormat.SHORT,
                    DateFormat.SHORT
                ).format(event.createdAt * 1000L),
            11,
            MID
        );
        source.setPadding(0, dp(6), 0, 0);
        card.addView(source, matchWrap());

        if ("active".equals(event.status) || "snoozed".equals(event.status)) {
            LinearLayout actions = new LinearLayout(this);
            actions.setOrientation(LinearLayout.HORIZONTAL);
            actions.setPadding(0, dp(10), 0, 0);
            if (event.hasAction("view_camera")) {
                actions.addView(
                    actionButton("View camera", view -> openHomeAssistant(event)),
                    actionParams()
                );
            }
            if (ProactiveUiPolicy.mayTurnOff(event)) {
                actions.addView(
                    actionButton("Turn off", view -> action(event, "turn_off")),
                    actionParams()
                );
            }
            if (event.hasAction("remind_later")) {
                actions.addView(
                    actionButton("Remind 15m", view -> action(event, "remind_later")),
                    actionParams()
                );
            }
            if (event.hasAction("dismiss")) {
                actions.addView(
                    actionButton("Dismiss", view -> action(event, "dismiss")),
                    actionParams()
                );
            }
            card.addView(actions, matchWrap());
        }
        return card;
    }

    private void action(ProactiveEvent event, String action) {
        status.setText("Sending action");
        client.action(event, action, 15, new ProactiveClient.ResultCallback() {
            @Override public void onSuccess() {
                load();
            }
            @Override public void onError(String message) {
                status.setText("Action failed");
                Toast.makeText(
                    ProactiveActivity.this,
                    message,
                    Toast.LENGTH_LONG
                ).show();
            }
        });
    }

    private void openHomeAssistant(ProactiveEvent event) {
        String base = store.homeAssistantUrl();
        if (base.isBlank()) {
            Toast.makeText(
                this,
                "Add the Home Assistant URL in Settings",
                Toast.LENGTH_LONG
            ).show();
            return;
        }
        startActivity(
            new Intent(
                Intent.ACTION_VIEW,
                Uri.parse(
                    base + "/config/entities/entity/" + Uri.encode(event.entityId)
                )
            )
        );
    }

    private LinearLayout card() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(15), dp(14), dp(15), dp(14));
        card.setBackground(rounded(WHITE, 18, 1, LINE));
        return card;
    }

    private View toggleRow(
        String titleValue,
        String noteValue,
        Switch control
    ) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(0, dp(6), 0, dp(6));
        LinearLayout copy = new LinearLayout(this);
        copy.setOrientation(LinearLayout.VERTICAL);
        copy.addView(text(titleValue, 15, BLACK), matchWrap());
        TextView note = text(noteValue, 12, MID);
        note.setPadding(0, dp(2), dp(10), 0);
        copy.addView(note, matchWrap());
        row.addView(
            copy,
            new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        );
        control.setText("");
        control.setMinWidth(0);
        row.addView(control, wrap());
        return row;
    }

    private Button actionButton(String label, View.OnClickListener listener) {
        Button button = secondaryButton(label);
        button.setTextSize(12);
        button.setOnClickListener(listener);
        return button;
    }

    private LinearLayout.LayoutParams actionParams() {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT,
            dp(40)
        );
        params.rightMargin = dp(7);
        return params;
    }

    private TextView section(String value) {
        TextView title = text(value, 17, BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        return title;
    }

    private static String label(String value) {
        if (value == null || value.isBlank()) return "System";
        return value.substring(0, 1).toUpperCase(Locale.ROOT) + value.substring(1);
    }

    private Button primaryButton(String label) {
        Button button = new Button(this);
        button.setText(label);
        button.setAllCaps(false);
        button.setTextColor(WHITE);
        button.setTextSize(14);
        button.setBackground(rounded(BLACK, 16, 0, Color.TRANSPARENT));
        return button;
    }

    private Button secondaryButton(String label) {
        Button button = new Button(this);
        button.setText(label);
        button.setAllCaps(false);
        button.setTextColor(BLACK);
        button.setTextSize(14);
        button.setBackground(rounded(SOFT, 16, 1, LINE));
        return button;
    }

    private ImageButton iconButton(int icon, String description) {
        ImageButton button = new ImageButton(this);
        button.setImageResource(icon);
        button.setColorFilter(BLACK);
        button.setContentDescription(description);
        button.setPadding(dp(10), dp(10), dp(10), dp(10));
        button.setBackground(rounded(SOFT, 21, 0, Color.TRANSPARENT));
        return button;
    }

    private TextView text(String value, int size, int colour) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(colour);
        view.setTypeface(Typeface.create("sans-serif", Typeface.NORMAL));
        return view;
    }

    private GradientDrawable rounded(
        int colour,
        int radius,
        int strokeWidth,
        int strokeColour
    ) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(colour);
        drawable.setCornerRadius(dp(radius));
        if (strokeWidth > 0) drawable.setStroke(dp(strokeWidth), strokeColour);
        return drawable;
    }

    private LinearLayout.LayoutParams matchWrap() {
        return new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        );
    }

    private LinearLayout.LayoutParams matchWrap(int top, int bottom) {
        LinearLayout.LayoutParams params = matchWrap();
        params.setMargins(0, top, 0, bottom);
        return params;
    }

    private LinearLayout.LayoutParams wrap() {
        return new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        );
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
