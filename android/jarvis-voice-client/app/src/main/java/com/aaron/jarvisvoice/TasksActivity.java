package com.aaron.jarvisvoice;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONObject;

import java.time.OffsetDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;

/** Top-level Tasks destination for durable Jarvis work. */
public final class TasksActivity extends Activity {
    private static final String STATE_FILTER = "task_filter";
    private static final int BLACK = Color.rgb(20, 20, 20);
    private static final int MID = Color.rgb(103, 103, 103);
    private static final int LINE = Color.rgb(226, 226, 226);
    private static final int SOFT = Color.rgb(246, 246, 246);
    private static final String[] FILTERS = {
        "ACTIVE", "WAITING_FOR_YOU", "SCHEDULED", "COMPLETED", "PROBLEMS"
    };
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable refresher = this::refresh;
    private TaskCentreClient client;
    private LinearLayout taskList;
    private TextView status;
    private String filter = "ACTIVE";
    private boolean visible;
    private int refreshGeneration;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        if (state != null) filter = state.getString(STATE_FILTER, "ACTIVE");
        client = new TaskCentreClient(this);
        setContentView(build());
    }

    @Override protected void onSaveInstanceState(Bundle state) {
        state.putString(STATE_FILTER, filter);
        super.onSaveInstanceState(state);
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
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.WHITE);
        root.setPadding(dp(18), dp(18), dp(18), dp(12));

        TextView heading = text("J A R V I S", 18, BLACK);
        heading.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        heading.setLetterSpacing(.12f);
        root.addView(heading, matchWrap());
        root.addView(primaryNavigation(), matchWrap(0, dp(12)));

        TextView title = text("Tasks", 28, BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        root.addView(title, matchWrap(0, dp(14)));
        status = text("Loading tasks…", 13, MID);
        root.addView(status, matchWrap(0, dp(8)));
        root.addView(filters(), matchWrap(0, dp(14)));

        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        taskList = new LinearLayout(this);
        taskList.setOrientation(LinearLayout.VERTICAL);
        taskList.setPadding(0, 0, 0, dp(30));
        scroll.addView(taskList, new ScrollView.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        root.addView(scroll, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f
        ));
        return root;
    }

    private View primaryNavigation() {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setBackground(rounded(SOFT, 22, 0, Color.TRANSPARENT));
        TextView chat = tab("Chat", false);
        chat.setOnClickListener(view -> {
            Intent intent = new Intent(this, MainActivity.class);
            intent.addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT);
            startActivity(intent);
            finish();
        });
        row.addView(chat, new LinearLayout.LayoutParams(0, dp(42), 1f));
        row.addView(tab("Tasks", true), new LinearLayout.LayoutParams(0, dp(42), 1f));
        return row;
    }

    private View filters() {
        HorizontalScrollView scroll = new HorizontalScrollView(this);
        scroll.setHorizontalScrollBarEnabled(false);
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        for (String value : FILTERS) {
            Button button = new Button(this);
            button.setAllCaps(false);
            button.setText(filterLabel(value));
            button.setTextSize(12);
            button.setTextColor(BLACK);
            button.setBackground(rounded(value.equals(filter) ? LINE : SOFT, 18, 0, Color.TRANSPARENT));
            button.setSelected(value.equals(filter));
            button.setContentDescription(
                "Show " + filterLabel(value) + " tasks"
                    + (value.equals(filter) ? ", selected" : "")
            );
            button.setOnClickListener(view -> {
                filter = value;
                setContentView(build());
                refresh();
            });
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, dp(38)
            );
            params.setMarginEnd(dp(8));
            row.addView(button, params);
        }
        scroll.addView(row, new HorizontalScrollView.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        return scroll;
    }

    private void refresh() {
        handler.removeCallbacks(refresher);
        int generation = ++refreshGeneration;
        client.list(filter, new TaskCentreClient.ListCallback() {
            @Override public void onSuccess(List<TaskItem> tasks, JSONObject counts) {
                if (generation != refreshGeneration) return;
                render(tasks, counts);
                if (visible) handler.postDelayed(refresher, 15_000L);
            }

            @Override public void onError(String message) {
                if (generation != refreshGeneration) return;
                status.setText("Tasks are unavailable — " + message);
                renderEmpty("Jarvis couldn't refresh tasks. Pull back here in a moment.");
                if (visible) handler.postDelayed(refresher, 30_000L);
            }
        });
    }

    private void render(List<TaskItem> tasks, JSONObject counts) {
        taskList.removeAllViews();
        int active = counts.optInt("ACTIVE", 0);
        status.setText(active == 1 ? "1 active task" : active + " active tasks");
        if (tasks.isEmpty()) {
            renderEmpty(emptyMessage());
            return;
        }
        for (TaskItem item : tasks) taskList.addView(card(item), matchWrap(0, dp(10)));
    }

    private View card(TaskItem item) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(16), dp(15), dp(16), dp(14));
        card.setBackground(rounded(Color.WHITE, 18, 1, LINE));
        card.setClickable(true);
        card.setFocusable(true);
        card.setContentDescription(item.title + ", " + item.statusLabel());
        card.setOnClickListener(view -> {
            Intent intent = new Intent(this, TaskDetailActivity.class);
            intent.putExtra(TaskDetailActivity.EXTRA_TASK_ID, item.taskId);
            startActivity(intent);
        });

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        TextView title = text(item.title, 17, BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        header.addView(title, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView state = text(item.statusLabel(), 12, statusColor(item.status));
        state.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        header.addView(state, wrapWrap());
        card.addView(header, matchWrap());

        TextView activity = text(item.activityText(), 14, MID);
        activity.setPadding(0, dp(8), 0, 0);
        card.addView(activity, matchWrap());
        if ("email_cleanup".equals(item.taskType) && !item.plannedSteps.isEmpty()) {
            for (JSONObject provider : item.plannedSteps) {
                String name = provider.optString("title", "Provider");
                String providerState = provider.optString("status", "pending")
                    .replace('_', ' ');
                String result = provider.optString("result_summary", "");
                String line = name + ": " + providerState;
                if (!result.isBlank()) line += " — " + result;
                TextView providerView = text(line, 13, BLACK);
                providerView.setPadding(0, dp(6), 0, 0);
                card.addView(providerView, matchWrap());
            }
        }
        if (!item.progressText().isBlank()) {
            TextView progress = text(item.progressText(), 13, BLACK);
            progress.setPadding(0, dp(8), 0, 0);
            card.addView(progress, matchWrap());
            if (item.progressCurrent != null && item.progressTotal != null && item.progressTotal > 0) {
                ProgressBar bar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
                bar.setMax(item.progressTotal);
                bar.setProgress(Math.min(item.progressCurrent, item.progressTotal));
                bar.setProgressTintList(android.content.res.ColorStateList.valueOf(BLACK));
                bar.setProgressBackgroundTintList(android.content.res.ColorStateList.valueOf(LINE));
                card.addView(bar, new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, dp(3)
                ));
            }
        }
        if (!item.waitingReason.isBlank()) {
            TextView reason = text("Why: " + item.waitingReason, 13, BLACK);
            reason.setPadding(0, dp(8), 0, 0);
            card.addView(reason, matchWrap());
        }
        String time = relativeTime(item.updatedAt);
        if (!time.isBlank()) {
            TextView updated = text("Updated " + time, 12, MID);
            updated.setPadding(0, dp(8), 0, 0);
            card.addView(updated, matchWrap());
        }
        return card;
    }

    private void renderEmpty(String message) {
        taskList.removeAllViews();
        TextView empty = text(message, 15, MID);
        empty.setGravity(Gravity.CENTER);
        empty.setPadding(dp(22), dp(70), dp(22), dp(30));
        taskList.addView(empty, matchWrap());
    }

    private String emptyMessage() {
        return switch (filter) {
            case "WAITING_FOR_YOU" -> "Nothing needs you right now.";
            case "SCHEDULED" -> "Nothing is scheduled.";
            case "COMPLETED" -> "No completed task history yet.";
            case "PROBLEMS" -> "No blocked or failed tasks.";
            default -> "Jarvis isn't working on anything right now.";
        };
    }

    static String filterLabel(String value) {
        return switch (value) {
            case "WAITING_FOR_YOU" -> "Waiting for you";
            case "PROBLEMS" -> "Problems";
            case "COMPLETED" -> "Completed";
            case "SCHEDULED" -> "Scheduled";
            default -> "Active";
        };
    }

    static String relativeTime(String raw) {
        if (raw == null || raw.isBlank()) return "";
        try {
            OffsetDateTime value = OffsetDateTime.parse(raw);
            long seconds = Math.max(0, java.time.Duration.between(value, OffsetDateTime.now()).getSeconds());
            if (seconds < 60) return seconds + " seconds ago";
            if (seconds < 3600) return (seconds / 60) + " minutes ago";
            if (seconds < 86_400) return (seconds / 3600) + " hours ago";
            return value.format(DateTimeFormatter.ofPattern("d MMM, HH:mm"));
        } catch (Exception ignored) {
            return "recently";
        }
    }

    private TextView tab(String label, boolean selected) {
        TextView tab = text(label, 14, selected ? Color.WHITE : BLACK);
        tab.setGravity(Gravity.CENTER);
        tab.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        tab.setBackground(rounded(selected ? BLACK : Color.TRANSPARENT, 20, 0, Color.TRANSPARENT));
        tab.setContentDescription(label + (selected ? ", selected" : ""));
        return tab;
    }

    private int statusColor(String value) {
        if ("FAILED".equals(value) || "PARTIAL".equals(value)) return Color.rgb(160, 40, 32);
        if ("WAITING_FOR_YOU".equals(value)) return Color.rgb(126, 79, 0);
        return MID;
    }

    private TextView text(String value, float size, int color) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(color);
        view.setLineSpacing(0, 1.08f);
        return view;
    }

    private GradientDrawable rounded(int color, int radius, int stroke, int strokeColor) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(color);
        drawable.setCornerRadius(dp(radius));
        if (stroke > 0) drawable.setStroke(dp(stroke), strokeColor);
        return drawable;
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
    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
