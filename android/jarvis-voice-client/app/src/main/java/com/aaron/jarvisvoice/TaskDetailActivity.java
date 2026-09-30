package com.aaron.jarvisvoice;

import android.app.Activity;
import android.app.Dialog;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

/** Detailed evidence, timeline and valid controls for one durable task. */
public final class TaskDetailActivity extends Activity {
    public static final String EXTRA_TASK_ID = "task_id";
    private static final int BLACK = Color.rgb(20, 20, 20);
    private static final int MID = Color.rgb(103, 103, 103);
    private static final int LINE = Color.rgb(226, 226, 226);
    private static final int SOFT = Color.rgb(246, 246, 246);
    private TaskCentreClient client;
    private LinearLayout content;
    private String taskId;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        taskId = getIntent().getStringExtra(EXTRA_TASK_ID);
        if (taskId == null || taskId.isBlank()) {
            finish();
            return;
        }
        client = new TaskCentreClient(this);
        setContentView(build());
        load();
    }

    @Override protected void onDestroy() {
        if (client != null) client.close();
        super.onDestroy();
    }

    private View build() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.WHITE);
        root.setPadding(dp(18), dp(18), dp(18), dp(12));
        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        Button back = button("‹ Tasks", false);
        back.setContentDescription("Back to tasks");
        back.setOnClickListener(view -> finish());
        header.addView(back, wrapWrap());
        TextView heading = text("Task details", 21, BLACK);
        heading.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        heading.setGravity(Gravity.END);
        header.addView(heading, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        root.addView(header, matchWrap());
        ScrollView scroll = new ScrollView(this);
        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(0, dp(18), 0, dp(32));
        TextView loading = text("Loading task…", 14, MID);
        content.addView(loading, matchWrap());
        scroll.addView(content, new ScrollView.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        root.addView(scroll, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f
        ));
        return root;
    }

    private void load() {
        client.task(taskId, new TaskCentreClient.TaskCallback() {
            @Override public void onSuccess(TaskItem task) { render(task); }
            @Override public void onError(String message) {
                content.removeAllViews();
                content.addView(text("This task is unavailable. " + message, 15, MID), matchWrap());
            }
        });
    }

    private void render(TaskItem task) {
        content.removeAllViews();
        TextView title = text(task.title, 25, BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        content.addView(title, matchWrap());
        TextView state = text(task.statusLabel(), 14, MID);
        state.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        state.setPadding(0, dp(5), 0, 0);
        content.addView(state, matchWrap());

        addFact("Current", task.activityText());
        addFact("Progress", task.progressText());
        addFact("Why it is waiting", task.waitingReason);
        addFact("Next", task.nextStep);
        addFact("Result", task.resultSummary);
        addFact("Problem", task.errorSummary);
        addFact("Providers", String.join(", ", task.providers));
        addFact("Capabilities", String.join(", ", task.capabilities));
        addFact("Created", displayTime(task.createdAt));
        addFact("Started", displayTime(task.startedAt));
        addFact("Last updated", TasksActivity.relativeTime(task.updatedAt));
        addFact("Completed", displayTime(task.completedAt));
        addFact(
            "Completion notification",
            notificationState(task.notificationOnCompletion, task.completionNotificationState)
        );
        addFact(
            "Failure notification",
            notificationState(task.notificationOnFailure, task.failureNotificationState)
        );
        addFact("Notification delivered", displayTime(task.notificationDeliveredAt));

        if (!task.plannedSteps.isEmpty()) {
            boolean hasCompleted = false;
            boolean hasRemaining = false;
            for (JSONObject step : task.plannedSteps) {
                String status = step.optString("status", "pending").replace('_', ' ');
                if ("succeeded".equals(status)) hasCompleted = true;
                else hasRemaining = true;
            }
            if (hasCompleted) {
                section("Completed steps");
                renderPlanSteps(task, true);
            }
            if (hasRemaining) {
                section("Planned remaining steps");
                renderPlanSteps(task, false);
            }
        }
        if (!task.timeline.isEmpty()) {
            section("Timeline");
            for (JSONObject event : task.timeline) {
                addTimelineRow(
                    event.optString("summary", "Task updated"),
                    TasksActivity.relativeTime(event.optString("at", ""))
                );
            }
        }
        if (task.isActive()) {
            section("Notifications");
            Button completion = button(
                task.notificationOnCompletion
                    ? "Stop completion notification" : "Notify me when done",
                task.notificationOnCompletion
            );
            completion.setOnClickListener(view -> client.notifications(
                task.taskId, !task.notificationOnCompletion, task.notificationOnFailure,
                refreshingCallback("Notification preference updated")
            ));
            content.addView(completion, matchWrap(0, dp(8)));
            Button failure = button(
                task.notificationOnFailure
                    ? "Stop failure notification" : "Notify me if it fails",
                task.notificationOnFailure
            );
            failure.setOnClickListener(view -> client.notifications(
                task.taskId, task.notificationOnCompletion, !task.notificationOnFailure,
                refreshingCallback("Notification preference updated")
            ));
            content.addView(failure, matchWrap(0, dp(8)));
        }

        if (hasControls(task)) {
            section("Actions");
            if (task.canConfirm) {
                addAction("Confirm exact task", "confirm", true);
            }
            if (task.canDecline) {
                addAction("Decline", "decline", false);
            }
            if (task.canRetry) addAction("Retry safely", "retry", true);
            if (task.canPause) addAction("Pause", "pause", false);
            if (task.canResume) addAction("Resume", "resume", true);
            if (task.canCancel) addAction("Cancel", "cancel", false);
            if (task.canSteer) {
                Button steer = button("Change task", false);
                steer.setOnClickListener(view -> showSteerDialog());
                content.addView(steer, matchWrap(0, dp(8)));
            }
            if (task.canReschedule) {
                Button reschedule = button("Reschedule", false);
                reschedule.setOnClickListener(view -> showRescheduleDialog());
                content.addView(reschedule, matchWrap(0, dp(8)));
            }
            if (!task.openChatConversationId.isBlank()) {
                Button chat = button("Open related chat", false);
                chat.setOnClickListener(view -> openChat(task.openChatConversationId));
                content.addView(chat, matchWrap(0, dp(8)));
            }
        }
    }

    private boolean hasControls(TaskItem task) {
        return task.canConfirm || task.canDecline || task.canRetry || task.canPause || task.canResume
            || task.canCancel || task.canSteer || task.canReschedule
            || !task.openChatConversationId.isBlank();
    }

    private void addFact(String label, String value) {
        if (value == null || value.isBlank()) return;
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(14), dp(12), dp(14), dp(12));
        card.setBackground(rounded(SOFT, 14, 0, Color.TRANSPARENT));
        TextView name = text(label, 12, MID);
        name.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        card.addView(name, matchWrap());
        TextView detail = text(value, 15, BLACK);
        detail.setPadding(0, dp(4), 0, 0);
        card.addView(detail, matchWrap());
        content.addView(card, matchWrap(dp(10), 0));
    }

    private static String displayTime(String value) {
        if (value == null || value.isBlank()) return "";
        try {
            return java.time.OffsetDateTime.parse(value).format(
                java.time.format.DateTimeFormatter.ofPattern("d MMM, HH:mm")
            );
        } catch (Exception ignored) {
            return value;
        }
    }

    private static String notificationState(boolean requested, String state) {
        if (!requested && "not_requested".equals(state)) return "";
        return state.replace('_', ' ');
    }

    private void section(String name) {
        TextView heading = text(name, 17, BLACK);
        heading.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        content.addView(heading, matchWrap(dp(22), dp(5)));
    }

    private void addTimelineRow(String title, String detail) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.VERTICAL);
        row.setPadding(dp(12), dp(9), dp(12), dp(9));
        TextView name = text(title, 14, BLACK);
        row.addView(name, matchWrap());
        if (detail != null && !detail.isBlank()) {
            TextView state = text(detail, 12, MID);
            state.setPadding(0, dp(3), 0, 0);
            row.addView(state, matchWrap());
        }
        content.addView(row, matchWrap());
    }

    private void renderPlanSteps(TaskItem task, boolean completed) {
        for (JSONObject step : task.plannedSteps) {
            String rawStatus = step.optString("status", "pending");
            if (("succeeded".equals(rawStatus)) != completed) continue;
            String detail = rawStatus.replace('_', ' ');
            String result = step.optString("result_summary", "");
            String failure = step.optString("failure", "");
            if (!result.isBlank()) detail += " · " + result;
            else if (!failure.isBlank()) detail += " · " + failure;
            addTimelineRow(step.optString("title", "Task step"), detail);
        }
    }

    private void addAction(String label, String action, boolean primary) {
        Button button = button(label, primary);
        button.setOnClickListener(view -> client.action(
            taskId, action, refreshingCallback(label + " requested")
        ));
        content.addView(button, matchWrap(0, dp(8)));
    }

    private TaskCentreClient.TaskCallback refreshingCallback(String success) {
        return new TaskCentreClient.TaskCallback() {
            @Override public void onSuccess(TaskItem task) {
                Toast.makeText(TaskDetailActivity.this, success, Toast.LENGTH_SHORT).show();
                load();
            }
            @Override public void onError(String message) {
                Toast.makeText(TaskDetailActivity.this, message, Toast.LENGTH_LONG).show();
            }
        };
    }

    private void showSteerDialog() {
        Dialog dialog = new Dialog(this);
        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(dp(22), dp(22), dp(22), dp(18));
        panel.setBackground(rounded(Color.WHITE, 20, 0, Color.TRANSPARENT));
        TextView title = text("Change task", 20, BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        panel.addView(title, matchWrap());
        EditText instruction = new EditText(this);
        instruction.setHint("What should Jarvis change?");
        instruction.setTextSize(15);
        instruction.setMinLines(2);
        instruction.setMaxLines(5);
        instruction.setBackground(rounded(SOFT, 14, 1, LINE));
        instruction.setPadding(dp(12), dp(10), dp(12), dp(10));
        panel.addView(instruction, matchWrap(dp(14), dp(12)));
        Button apply = button("Apply change", true);
        apply.setOnClickListener(view -> {
            String value = instruction.getText().toString().trim();
            if (value.isBlank()) return;
            dialog.dismiss();
            client.steer(taskId, value, refreshingCallback("Task change requested"));
        });
        panel.addView(apply, matchWrap());
        dialog.setContentView(panel);
        dialog.show();
        instruction.requestFocus();
        dialog.getWindow().setSoftInputMode(
            android.view.WindowManager.LayoutParams.SOFT_INPUT_STATE_ALWAYS_VISIBLE
        );
    }

    private void showRescheduleDialog() {
        Dialog dialog = new Dialog(this);
        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(dp(22), dp(22), dp(22), dp(18));
        panel.setBackground(rounded(Color.WHITE, 20, 0, Color.TRANSPARENT));
        TextView title = text("Reschedule task", 20, BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        panel.addView(title, matchWrap());
        EditText due = new EditText(this);
        due.setHint("2026-09-23T09:00:00+01:00");
        due.setContentDescription("New scheduled date and time with timezone offset");
        due.setSingleLine(true);
        due.setTextSize(15);
        due.setBackground(rounded(SOFT, 14, 1, LINE));
        due.setPadding(dp(12), dp(10), dp(12), dp(10));
        panel.addView(due, matchWrap(dp(14), dp(12)));
        Button apply = button("Reschedule", true);
        apply.setOnClickListener(view -> {
            String value = due.getText().toString().trim();
            if (value.isBlank()) return;
            dialog.dismiss();
            client.reschedule(
                taskId,
                value,
                java.time.ZoneId.systemDefault().getId(),
                refreshingCallback("Task rescheduled")
            );
        });
        panel.addView(apply, matchWrap());
        dialog.setContentView(panel);
        dialog.show();
        due.requestFocus();
        dialog.getWindow().setSoftInputMode(
            android.view.WindowManager.LayoutParams.SOFT_INPUT_STATE_ALWAYS_VISIBLE
        );
    }

    private void openChat(String conversationId) {
        startService(
            new Intent(this, VoiceService.class)
                .setAction(VoiceService.ACTION_SWITCH_CHAT)
                .putExtra(VoiceService.EXTRA_CONVERSATION_ID, conversationId)
        );
        Intent intent = new Intent(this, MainActivity.class);
        intent.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        startActivity(intent);
    }

    private Button button(String label, boolean primary) {
        Button button = new Button(this);
        button.setAllCaps(false);
        button.setText(label);
        button.setTextSize(14);
        button.setTextColor(primary ? Color.WHITE : BLACK);
        button.setBackground(rounded(primary ? BLACK : SOFT, 20, primary ? 0 : 1, LINE));
        button.setMinHeight(dp(44));
        return button;
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
