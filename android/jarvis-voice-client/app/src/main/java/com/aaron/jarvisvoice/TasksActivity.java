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
        "ACTIVE", "WAITING_FOR_YOU", "SCHEDULED", "COMPLETED"
    };
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable refresher = this::refresh;
    private TaskCentreClient client;
    private LinearLayout taskList;
    private TextView status;
    private Button retry;
    private String filter = "ACTIVE";
    private boolean visible;
    private int refreshGeneration;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        if (state != null) filter = state.getString(STATE_FILTER, "ACTIVE");
        client = new TaskCentreClient(this);
        setContentView(build());
        renderCachedSnapshot();
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
        LinearLayout connectionRow = new LinearLayout(this);
        connectionRow.setOrientation(LinearLayout.HORIZONTAL);
        connectionRow.setGravity(Gravity.CENTER_VERTICAL);
        status = text("Loading tasks…", 13, MID);
        connectionRow.addView(status, new LinearLayout.LayoutParams(
            0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f
        ));
        retry = button("Retry");
        retry.setVisibility(View.GONE);
        retry.setOnClickListener(view -> refresh());
        connectionRow.addView(retry, wrapWrap());
        root.addView(connectionRow, matchWrap(0, dp(8)));
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
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        for (String value : FILTERS) {
            Button button = new Button(this);
            button.setAllCaps(false);
            button.setText(filterLabel(value));
            button.setTextSize(12);
            button.setTextColor(BLACK);
            button.setBackground(rounded(value.equals(filter) ? LINE : SOFT, 18, 0, Color.TRANSPARENT));
            button.setMinWidth(0);
            button.setMinimumWidth(0);
            button.setPadding(dp(3), 0, dp(3), 0);
            button.setSelected(value.equals(filter));
            button.setContentDescription(
                "Show " + filterAccessibilityLabel(value) + " tasks"
                    + (value.equals(filter) ? ", selected" : "")
            );
            button.setOnClickListener(view -> {
                filter = value;
                setContentView(build());
                refresh();
            });
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                0, dp(38), 1f
            );
            if (!value.equals(FILTERS[FILTERS.length - 1])) params.setMarginEnd(dp(5));
            row.addView(button, params);
        }
        return row;
    }

    private void refresh() {
        handler.removeCallbacks(refresher);
        int generation = ++refreshGeneration;
        client.list(filter, new TaskCentreClient.ListCallback() {
            @Override public void onSuccess(List<TaskItem> tasks, JSONObject counts) {
                if (generation != refreshGeneration) return;
                retry.setVisibility(View.GONE);
                render(tasks, counts, false, System.currentTimeMillis());
                if (visible) handler.postDelayed(refresher, 15_000L);
            }

            @Override public void onError(String message) {
                if (generation != refreshGeneration) return;
                retry.setVisibility(View.VISIBLE);
                TaskCentreClient.CachedList cached = client.cached(filter);
                if (cached != null && !cached.tasks().isEmpty()) {
                    render(cached.tasks(), cached.counts(), true, cached.receivedAtMillis());
                } else {
                    status.setText("Can't reach Jarvis Core. Reconnecting…");
                    renderEmpty("Tasks will appear when Jarvis reconnects.");
                }
                if (visible) handler.postDelayed(refresher, 30_000L);
            }
        });
    }

    private void renderCachedSnapshot() {
        TaskCentreClient.CachedList cached = client.cached(filter);
        if (cached != null && !cached.tasks().isEmpty()) {
            render(cached.tasks(), cached.counts(), true, cached.receivedAtMillis());
        }
    }

    private void render(
        List<TaskItem> tasks,
        JSONObject counts,
        boolean stale,
        long receivedAtMillis
    ) {
        taskList.removeAllViews();
        int active = counts.optInt("ACTIVE", 0);
        if (stale) {
            status.setText(
                "Reconnecting… Last synced " + relativeTimeMillis(receivedAtMillis)
            );
        } else {
            status.setText(
                "Synced " + relativeTimeMillis(receivedAtMillis) + " · "
                    + (active == 1 ? "1 active task" : active + " active tasks")
            );
        }
        if (tasks.isEmpty()) {
            renderEmpty(emptyMessage());
            return;
        }
        for (TaskItem item : tasks) {
            taskList.addView(card(item, stale, receivedAtMillis), matchWrap(0, dp(12)));
        }
    }

    private View card(TaskItem item, boolean stale) {
        long syncAt = client == null ? 0L : client.lastSuccessfulSyncAt();
        return card(item, stale, syncAt > 0L ? syncAt : System.currentTimeMillis());
    }

    private View card(TaskItem item, boolean stale, long syncAtMillis) {
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

        TextView activity = text(item.activityText(), 15, MID);
        activity.setPadding(0, dp(8), 0, 0);
        card.addView(activity, matchWrap());

        if (!item.backlogTitle().isBlank() || !item.backlogSummary().isBlank()) {
            String backlogTitle = item.backlogTitle();
            String stateLabel = item.backlogStatusLabel();
            String headingText = backlogTitle.isBlank()
                ? stateLabel
                : stateLabel.isBlank()
                    ? backlogTitle
                    : backlogTitle + " " + stateLabel.toLowerCase();
            if (!headingText.isBlank()) {
                TextView historyTitle = text(headingText, 13, BLACK);
                historyTitle.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
                historyTitle.setPadding(0, dp(12), 0, 0);
                card.addView(historyTitle, matchWrap());
            }
            if (!item.backlogSummary().isBlank()) {
                card.addView(text(item.backlogSummary(), 13, MID), matchWrap(0, dp(3)));
            }
        }

        if (!item.percentText().isBlank()) {
            TextView percent = text(item.percentText(), 22, BLACK);
            percent.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
            percent.setPadding(0, dp(14), 0, dp(5));
            card.addView(percent, matchWrap());
        }
        if (!item.progressText().isBlank()) {
            if ("DETERMINATE".equals(item.progressMode) && item.progressFraction != null) {
                ProgressBar bar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
                bar.setMax(1000);
                bar.setProgress((int) Math.round(
                    Math.max(0d, Math.min(item.progressFraction, 1d)) * 1000d
                ));
                bar.setProgressTintList(android.content.res.ColorStateList.valueOf(BLACK));
                bar.setProgressBackgroundTintList(android.content.res.ColorStateList.valueOf(LINE));
                card.addView(bar, new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, dp(5)
                ));
            }
            LinearLayout progressRow = new LinearLayout(this);
            progressRow.setOrientation(LinearLayout.HORIZONTAL);
            TextView progress = text(item.progressText(), 13, BLACK);
            progress.setPadding(0, dp(8), 0, 0);
            progressRow.addView(progress, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f
            ));
            if (!item.remainingText().isBlank()) {
                TextView remaining = text(item.remainingText(), 13, MID);
                remaining.setPadding(dp(8), dp(8), 0, 0);
                progressRow.addView(remaining, wrapWrap());
            }
            card.addView(progressRow, matchWrap());
        }
        if (!item.etaText().isBlank()) {
            TextView estimateLabel = text("Estimated time remaining", 12, MID);
            estimateLabel.setPadding(0, dp(12), 0, 0);
            card.addView(estimateLabel, matchWrap());
            card.addView(text(item.etaText(), 14, BLACK), matchWrap(0, dp(2)));
        }
        if (!item.primaryMetricText().isBlank() && item.backlogSummary().isBlank()) {
            TextView metric = text(item.primaryMetricText(), 14, BLACK);
            metric.setPadding(0, dp(12), 0, 0);
            card.addView(metric, matchWrap());
        }
        for (JSONObject subtask : item.subtasks) {
            addSubtask(card, subtask);
        }
        if (!item.waitingReason.isBlank()) {
            TextView reason = text(item.waitingReason, 13, BLACK);
            reason.setPadding(0, dp(8), 0, 0);
            card.addView(reason, matchWrap());
        }
        String time = relativeTimeMillis(syncAtMillis);
        if (!time.isBlank()) {
            TextView updated = text((stale ? "Last synced " : "Synced ") + time, 12, MID);
            updated.setPadding(0, dp(8), 0, 0);
            card.addView(updated, matchWrap());
        }
        TextView details = text("View details", 13, BLACK);
        details.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        details.setPadding(0, dp(10), 0, 0);
        card.addView(details, matchWrap());
        return card;
    }

    private void addSubtask(LinearLayout card, JSONObject subtask) {
        LinearLayout section = new LinearLayout(this);
        section.setOrientation(LinearLayout.VERTICAL);
        section.setPadding(dp(12), dp(10), dp(12), dp(10));
        section.setBackground(rounded(SOFT, 12, 0, Color.TRANSPARENT));
        LinearLayout heading = new LinearLayout(this);
        heading.setOrientation(LinearLayout.HORIZONTAL);
        String title = TaskItem.optionalString(subtask, "title", "Provider");
        TextView name = text(title, 14, BLACK);
        name.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        heading.addView(name, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        heading.addView(text(providerStatus(subtask.optString("status", "")), 12, MID), wrapWrap());
        section.addView(heading, matchWrap());
        JSONObject progress = subtask.optJSONObject("progress");
        if (progress != null && !progress.isNull("current")) {
            long current = Math.max(0L, progress.optLong("current", 0L));
            TextView reviewed = text(String.format("%,d reviewed", current), 13, BLACK);
            reviewed.setPadding(0, dp(5), 0, 0);
            section.addView(reviewed, matchWrap());
        }
        JSONObject backlog = subtask.optJSONObject("backlog");
        if (backlog != null) {
            String backlogSummary = TaskItem.optionalString(backlog, "summary", "");
            if (!backlogSummary.isBlank()) {
                TextView history = text(backlogSummary, 13, BLACK);
                history.setPadding(0, dp(5), 0, 0);
                section.addView(history, matchWrap());
            }
        }
        org.json.JSONArray metrics = subtask.optJSONArray("metrics");
        if (metrics != null) {
            for (int index = 0; index < metrics.length(); index++) {
                JSONObject metric = metrics.optJSONObject(index);
                if (metric == null || !metric.optBoolean("primary", false)) continue;
                long value = metric.optLong("value", 0L);
                String label = TaskItem.optionalString(metric, "label", "Result").toLowerCase();
                String destination = TaskItem.optionalString(metric, "destination", "");
                section.addView(text(
                    String.format("%,d %s%s", value, label,
                        destination.isBlank() ? "" : " to " + destination),
                    13,
                    MID
                ), matchWrap(0, 0));
            }
        }
        card.addView(section, matchWrap(dp(10), 0));
    }

    private static String providerStatus(String raw) {
        return switch (raw == null ? "" : raw.toLowerCase()) {
            case "running", "pending" -> "Running";
            case "monitoring" -> "Monitoring";
            case "paused" -> "Paused";
            case "waiting_provider", "waiting_for_jarvis" -> "Waiting";
            case "failed" -> "Needs attention";
            case "completed" -> "Completed";
            default -> "";
        };
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
            case "WAITING_FOR_YOU" -> "Waiting";
            case "PROBLEMS" -> "Problems";
            case "COMPLETED" -> "Completed";
            case "SCHEDULED" -> "Scheduled";
            default -> "Active";
        };
    }

    static String filterAccessibilityLabel(String value) {
        return "WAITING_FOR_YOU".equals(value) ? "Waiting for you" : filterLabel(value);
    }

    static String relativeTime(String raw) {
        if (raw == null || raw.isBlank()) return "";
        try {
            OffsetDateTime value = OffsetDateTime.parse(raw);
            long seconds = Math.max(0, java.time.Duration.between(value, OffsetDateTime.now()).getSeconds());
            if (seconds < 10) return "just now";
            if (seconds < 60) return seconds + " seconds ago";
            if (seconds < 3600) return (seconds / 60) + " min ago";
            if (seconds < 86_400) return (seconds / 3600) + " hr ago";
            return value.format(DateTimeFormatter.ofPattern("d MMM, HH:mm"));
        } catch (Exception ignored) {
            return "recently";
        }
    }

    static String relativeTimeMillis(long timestamp) {
        if (timestamp <= 0L) return "an earlier update";
        long seconds = Math.max(0L, (System.currentTimeMillis() - timestamp) / 1000L);
        if (seconds < 10L) return "just now";
        if (seconds < 60L) return seconds + " seconds ago";
        if (seconds < 3600L) return (seconds / 60L) + " min ago";
        return (seconds / 3600L) + " hr ago";
    }

    private Button button(String label) {
        Button button = new Button(this);
        button.setAllCaps(false);
        button.setText(label);
        button.setTextSize(12);
        button.setTextColor(BLACK);
        button.setMinHeight(0);
        button.setMinimumHeight(0);
        button.setPadding(dp(12), dp(5), dp(12), dp(5));
        button.setBackground(rounded(SOFT, 14, 0, Color.TRANSPARENT));
        return button;
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
