package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;
import static org.robolectric.Shadows.shadowOf;

import android.content.Intent;
import android.content.pm.ActivityInfo;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;

import androidx.work.Configuration;
import androidx.work.WorkManager;

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

import java.io.IOException;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35)
public final class TaskCentreUiTest {
    @Before public void clearTaskSnapshotsBeforeTest() {
        RuntimeEnvironment.getApplication()
            .getSharedPreferences("jarvis_task_snapshots_v1", android.content.Context.MODE_PRIVATE)
            .edit().clear().commit();
    }

    @After public void clearTaskSnapshotsAfterTest() {
        RuntimeEnvironment.getApplication()
            .getSharedPreferences("jarvis_task_snapshots_v1", android.content.Context.MODE_PRIVATE)
            .edit().clear().commit();
    }

    @Test public void taskProjectionUsesMeasuredProgressAndNaturalStatuses() throws Exception {
        JSONObject value = new JSONObject()
            .put("task_id", "email_bulk:one")
            .put("task_type", "email_cleanup")
            .put("title", "Inbox cleanup")
            .put("status", "WAITING_FOR_YOU")
            .put("underlying_status", "awaiting_confirmation")
            .put("current_step", "Waiting for confirmation")
            .put("progress_current", 0)
            .put("progress_total", 46502)
            .put("progress_unit", "messages")
            .put("requires_user_action", true)
            .put("user_action_type", "confirmation")
            .put("planned_steps", new JSONArray())
            .put("timeline", new JSONArray());

        TaskItem task = TaskItem.fromJson(value);

        assertEquals("Waiting for you", task.statusLabel());
        assertEquals("0 of 46,502 reviewed", task.progressText());
        assertEquals("Waiting for confirmation", task.activityText());
        assertTrue(task.isActive());
        assertTrue(task.requiresUserAction);
    }

    @Test public void taskProjectionNeverManufacturesProgress() throws Exception {
        TaskItem task = TaskItem.fromJson(new JSONObject()
            .put("task_id", "executive:one")
            .put("title", "Research amplifiers")
            .put("status", "RUNNING"));

        assertEquals("", task.progressText());
        assertEquals("Running", task.activityText());
        assertFalse(task.notificationOnCompletion);
    }

    @Test public void structuredProgressRendersGroundedCountsEtaAndProviders() throws Exception {
        JSONArray subtasks = new JSONArray();
        subtasks.put(new JSONObject()
            .put("title", "Gmail")
            .put("status", "running")
            .put("progress", new JSONObject()
                .put("mode", "INDETERMINATE")
                .put("current", 10_598)
                .put("unit", "messages"))
            .put("metrics", new JSONArray().put(new JSONObject()
                .put("key", "moved")
                .put("label", "Moved")
                .put("value", 6_289)
                .put("destination", "Bin")
                .put("primary", true))));
        subtasks.put(new JSONObject()
            .put("title", "Outlook")
            .put("status", "running")
            .put("progress", new JSONObject()
                .put("mode", "INDETERMINATE")
                .put("current", 33_400)
                .put("unit", "messages"))
            .put("metrics", new JSONArray().put(new JSONObject()
                .put("key", "moved")
                .put("label", "Moved")
                .put("value", 999)
                .put("destination", "Deleted Items")
                .put("primary", true))));
        JSONObject value = new JSONObject()
            .put("task_id", "important_only:inbox")
            .put("title", "Important-Only Inbox")
            .put("status", "RUNNING")
            .put("current_step", "Reviewing your existing inbox")
            .put("updated_at", java.time.OffsetDateTime.now().toString())
            .put("progress", new JSONObject()
                .put("mode", "DETERMINATE")
                .put("current", 43_998)
                .put("total", 50_654)
                .put("remaining", 6_656)
                .put("fraction", 43_998d / 50_654d)
                .put("percent", 87)
                .put("unit", "messages"))
            .put("timing", new JSONObject()
                .put("eta_seconds", 18 * 60)
                .put("eta_quality", "smoothed_rolling_15_minute"))
            .put("metrics", new JSONArray().put(new JSONObject()
                .put("key", "moved")
                .put("label", "Moved to deleted folders")
                .put("value", 7_288)
                .put("primary", true)))
            .put("subtasks", subtasks);
        TaskItem task = TaskItem.fromJson(value);

        assertEquals("87% complete", task.percentText());
        assertEquals("43,998 of 50,654 reviewed", task.progressText());
        assertEquals("6,656 remaining", task.remainingText());
        assertEquals("About 18 minutes", task.etaText());

        TasksActivity activity = Robolectric.buildActivity(TasksActivity.class).create().get();
        java.lang.reflect.Method renderer = TasksActivity.class.getDeclaredMethod(
            "card", TaskItem.class, boolean.class
        );
        renderer.setAccessible(true);
        View card = (View) renderer.invoke(activity, task, false);
        assertNotNull(findText(card, "87% complete"));
        assertNotNull(findText(card, "6,656 remaining"));
        assertNotNull(findText(card, "7,288 moved to deleted folders"));
        assertNotNull(findText(card, "10,598 reviewed"));
        assertNotNull(findText(card, "6,289 moved to Bin"));
        assertNotNull(findText(card, "999 moved to Deleted Items"));
        assertNotNull(findText(card, "View details"));
        activity.onDestroy();
    }

    @Test public void unknownTotalAndPausedStateHidePercentAndEta() throws Exception {
        TaskItem task = TaskItem.fromJson(new JSONObject()
            .put("task_id", "generic:one")
            .put("title", "Review results")
            .put("status", "PAUSED")
            .put("progress", new JSONObject()
                .put("mode", "INDETERMINATE")
                .put("current", 400)
                .put("unit", "items"))
            .put("timing", new JSONObject()
                .put("eta_seconds", JSONObject.NULL)
                .put("eta_quality", "unavailable")));

        assertEquals("", task.percentText());
        assertEquals("400 items", task.progressText());
        assertEquals("", task.remainingText());
        assertEquals("", task.etaText());
    }

    @Test public void continuousMonitoringShowsHistoryWithoutFakeRemainingOrEta()
        throws Exception {
        JSONObject value = new JSONObject()
            .put("task_id", "important_only:inbox")
            .put("title", "Important-Only Inbox")
            .put("status", "MONITORING")
            .put("work_mode", "CONTINUOUS")
            .put("phase", "MONITORING")
            .put("current_step", "Monitoring new mail")
            .put("activity_time_label", "Last mailbox activity")
            .put("updated_at", "2026-10-04T21:01:28Z")
            .put("progress", new JSONObject()
                .put("mode", "NONE")
                .put("current", JSONObject.NULL)
                .put("total", JSONObject.NULL)
                .put("remaining", JSONObject.NULL)
                .put("percent", JSONObject.NULL))
            .put("timing", new JSONObject()
                .put("eta_seconds", JSONObject.NULL)
                .put("eta_quality", "not_applicable"))
            .put("backlog", new JSONObject()
                .put("status", "COMPLETED")
                .put("title", "Initial cleanup")
                .put("summary", "48,513 messages reviewed · 7,380 moved to deleted folders")
                .put("reviewed_count", 48_513)
                .put("moved_count", 7_380)
                .put("initial_estimate", 50_654))
            .put("subtasks", new JSONArray().put(new JSONObject()
                .put("title", "Gmail")
                .put("status", "monitoring")
                .put("progress", new JSONObject().put("mode", "NONE"))
                .put("backlog", new JSONObject()
                    .put("status", "COMPLETED")
                    .put("summary", "10,863 reviewed during initial cleanup"))
                .put("metrics", new JSONArray().put(new JSONObject()
                    .put("key", "moved")
                    .put("label", "Moved")
                    .put("value", 6_333)
                    .put("destination", "Bin")
                    .put("primary", true)))))
            .put("metadata", new JSONObject().put("policy_label", "Important-Only active"));
        TaskItem task = TaskItem.fromJson(value);

        assertEquals("Monitoring", task.statusLabel());
        assertTrue(task.isActive());
        assertEquals("", task.percentText());
        assertEquals("", task.progressText());
        assertEquals("", task.remainingText());
        assertEquals("", task.etaText());

        TasksActivity activity = Robolectric.buildActivity(TasksActivity.class).create().get();
        java.lang.reflect.Method renderer = TasksActivity.class.getDeclaredMethod(
            "card", TaskItem.class, boolean.class
        );
        renderer.setAccessible(true);
        View card = (View) renderer.invoke(activity, task, false);
        java.util.List<String> text = flattenText(card);
        assertTrue(text.contains("Monitoring"));
        assertTrue(text.contains("Initial cleanup complete"));
        assertTrue(text.contains("48,513 reviewed · 7,380 removed"));
        assertTrue(text.contains("10,863 reviewed during initial cleanup"));
        assertTrue(text.contains("Smart Inbox"));
        assertTrue(text.stream().noneMatch(item -> item.contains("Important-Only Inbox")));
        assertTrue(text.stream().noneMatch(item -> item.contains("remaining")));
        assertTrue(text.stream().noneMatch(item -> item.contains("Calculating estimate")));
        assertTrue(text.stream().noneMatch(item -> item.contains("% complete")));
        activity.onDestroy();

        Intent detailIntent = new Intent(
            RuntimeEnvironment.getApplication(), TaskDetailActivity.class
        ).putExtra(TaskDetailActivity.EXTRA_TASK_ID, task.taskId);
        org.robolectric.android.controller.ActivityController<TaskDetailActivity> controller =
            Robolectric.buildActivity(TaskDetailActivity.class, detailIntent).create();
        TaskDetailActivity detail = controller.get();
        java.lang.reflect.Method detailRenderer = TaskDetailActivity.class.getDeclaredMethod(
            "render", TaskItem.class
        );
        detailRenderer.setAccessible(true);
        detailRenderer.invoke(detail, task);
        java.util.List<String> detailText = flattenText(
            detail.findViewById(android.R.id.content)
        );
        assertTrue(detailText.contains("Monitoring"));
        assertTrue(detailText.contains("Initial cleanup: Complete"));
        assertTrue(detailText.contains("Last mailbox activity"));
        assertTrue(detailText.contains("Last synced"));
        assertTrue(detailText.contains("Smart Inbox active"));
        assertTrue(detailText.contains("Smart Inbox"));
        assertTrue(detailText.stream().noneMatch(item -> item.contains("Important-Only Inbox")));
        assertTrue(detailText.stream().noneMatch(item -> item.contains("remaining")));
        assertTrue(detailText.stream().noneMatch(item -> item.contains("Estimated time")));
        controller.destroy();
    }

    @Test public void nullLikeWaitingReasonNeverRendersWhyNull() throws Exception {
        TaskItem task = TaskItem.fromJson(new JSONObject()
            .put("task_id", "generic:null-reason")
            .put("title", "Background task")
            .put("status", "RUNNING")
            .put("waiting_reason", JSONObject.NULL));
        assertEquals("", task.waitingReason);

        TasksActivity activity = Robolectric.buildActivity(TasksActivity.class).create().get();
        java.lang.reflect.Method renderer = TasksActivity.class.getDeclaredMethod(
            "card", TaskItem.class, boolean.class
        );
        renderer.setAccessible(true);
        View card = (View) renderer.invoke(activity, task, false);
        assertTrue(flattenText(card).stream().noneMatch(text -> text.contains("Why: null")));
        activity.onDestroy();
    }

    @Test public void rawTransportFailuresAreSanitized() {
        String message = TaskCentreClient.userMessage(new IOException(
            "failed to connect to /192.0.2.10 (port 8000) from /192.0.2.20"
        ));
        assertEquals("Can't reach Jarvis Core.", message);
        assertFalse(message.contains("192."));
        assertFalse(message.contains("port"));
    }

    @Test public void cachedTaskSnapshotRemainsVisibleOffline() throws Exception {
        JSONObject response = new JSONObject()
            .put("counts", new JSONObject().put("ACTIVE", 1))
            .put("tasks", new JSONArray().put(new JSONObject()
                .put("task_id", "important_only:inbox")
                .put("title", "Important-Only Inbox")
                .put("status", "RUNNING")
                .put("progress_current", 43_998)
                .put("progress_total", 50_654)
                .put("progress_unit", "messages")));
        new TaskSnapshotStore(RuntimeEnvironment.getApplication(), "aaron")
            .save("ACTIVE", response, System.currentTimeMillis() - 120_000L);

        TasksActivity activity = Robolectric.buildActivity(TasksActivity.class).create().get();
        View root = activity.findViewById(android.R.id.content);
        assertNotNull(findText(root, "Smart Inbox"));
        assertTrue(flattenText(root).stream().noneMatch(text -> text.contains("Important-Only Inbox")));
        assertTrue(flattenText(root).stream().anyMatch(text -> text.contains("Reconnecting")));
        assertTrue(flattenText(root).stream().anyMatch(text -> text.contains("Last synced")));
        activity.onDestroy();
    }

    @Test public void refreshedSnapshotAdvancesSameDurableTaskWithoutDuplication() throws Exception {
        TaskSnapshotStore snapshots = new TaskSnapshotStore(
            RuntimeEnvironment.getApplication(), "aaron"
        );
        JSONObject first = taskListResponse("important_only:inbox", 43_998, 50_654);
        JSONObject advanced = taskListResponse("important_only:inbox", 44_050, 50_654);

        snapshots.save("ACTIVE", first, 1_000L);
        snapshots.save("ACTIVE", advanced, 2_000L);

        TaskSnapshotStore.Snapshot stored = snapshots.load("ACTIVE");
        assertNotNull(stored);
        JSONArray tasks = stored.response().getJSONArray("tasks");
        assertEquals(1, tasks.length());
        assertEquals("important_only:inbox", tasks.getJSONObject(0).getString("task_id"));
        assertEquals(44_050, tasks.getJSONObject(0).getInt("progress_current"));
        assertEquals(2_000L, stored.receivedAtMillis());
    }

    @Test public void successfulSyncFreshnessDoesNotDependOnTaskActivityTime() throws Exception {
        TaskItem unchanged = TaskItem.fromJson(new JSONObject()
            .put("task_id", "monitor:one")
            .put("title", "Continuous monitor")
            .put("status", "MONITORING")
            .put("updated_at", "2026-01-01T00:00:00Z")
            .put("progress", new JSONObject().put("mode", "NONE")));
        TasksActivity activity = Robolectric.buildActivity(TasksActivity.class).create().get();
        java.lang.reflect.Method renderer = TasksActivity.class.getDeclaredMethod(
            "render", java.util.List.class, JSONObject.class, boolean.class, long.class
        );
        renderer.setAccessible(true);
        renderer.invoke(
            activity,
            java.util.List.of(unchanged),
            new JSONObject().put("ACTIVE", 1),
            false,
            System.currentTimeMillis()
        );

        java.util.List<String> text = flattenText(activity.findViewById(android.R.id.content));
        assertTrue(text.stream().anyMatch(item -> item.startsWith("Synced just now")));
        assertTrue(text.stream().noneMatch(item -> item.startsWith("Updated ")));
        activity.onDestroy();
    }

    @Test public void phoneWidthsShowAllPrimaryFiltersWithoutClipping() throws Exception {
        TasksActivity activity = Robolectric.buildActivity(TasksActivity.class).create().get();
        java.lang.reflect.Method factory = TasksActivity.class.getDeclaredMethod("filters");
        factory.setAccessible(true);
        LinearLayout filters = (LinearLayout) factory.invoke(activity);
        for (int widthDp : new int[] {384, 320}) {
            int width = Math.round(
                widthDp * activity.getResources().getDisplayMetrics().density
            );
            filters.measure(
                View.MeasureSpec.makeMeasureSpec(width, View.MeasureSpec.EXACTLY),
                View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED)
            );
            filters.layout(0, 0, width, filters.getMeasuredHeight());
            assertEquals(width, filters.getMeasuredWidth());
            assertEquals(4, filters.getChildCount());
            assertTrue(filters.getChildAt(3).getRight() <= width);
        }
        assertNotNull(findText(filters, "Active"));
        assertNotNull(findText(filters, "Waiting"));
        assertNotNull(findText(filters, "Scheduled"));
        assertNotNull(findText(filters, "Completed"));
        activity.onDestroy();
    }

    @Test public void unseenCapabilityDomainNeedsNoDomainSpecificAndroidCode() throws Exception {
        JSONObject value = new JSONObject()
            .put("task_id", "agent_plan:appointment-plan")
            .put("source", "agent_plan")
            .put("source_task_id", "appointment-plan")
            .put("task_type", "multi_tool_plan")
            .put("title", "Book the appointment")
            .put("status", "WAITING_FOR_YOU")
            .put("current_step", "Book the selected appointment")
            .put("current_step_index", 2)
            .put("step_count", 2)
            .put("progress_current", 1)
            .put("progress_total", 2)
            .put("progress_unit", "steps")
            .put("requires_user_action", true)
            .put("user_action_type", "confirmation")
            .put("can_confirm", true)
            .put("can_decline", true)
            .put("providers", new JSONArray().put("appointments"))
            .put("capabilities", new JSONArray()
                .put("appointments.search")
                .put("appointments.book"))
            .put("planned_steps", new JSONArray()
                .put(new JSONObject()
                    .put("step_id", "find-slot")
                    .put("title", "Find an available appointment")
                    .put("status", "succeeded"))
                .put(new JSONObject()
                    .put("step_id", "book-slot")
                    .put("title", "Book the selected appointment")
                    .put("status", "awaiting_approval")))
            .put("timeline", new JSONArray());

        TaskItem task = TaskItem.fromJson(value);

        assertEquals("agent_plan", task.source);
        assertEquals("Waiting for you", task.statusLabel());
        assertEquals("1 of 2 steps", task.progressText());
        assertEquals("appointments", task.providers.get(0));
        assertEquals("appointments.book", task.capabilities.get(1));
        assertTrue(task.canConfirm);
        assertTrue(task.canDecline);
        assertEquals(2, task.plannedSteps.size());

        TasksActivity activity = Robolectric.buildActivity(TasksActivity.class).create().get();
        java.lang.reflect.Method renderer = TasksActivity.class.getDeclaredMethod(
            "card", TaskItem.class, boolean.class
        );
        renderer.setAccessible(true);
        View card = (View) renderer.invoke(activity, task, false);
        assertNotNull(findText(card, "Book the selected appointment"));
        activity.onDestroy();

        Intent detailIntent = new Intent(
            RuntimeEnvironment.getApplication(), TaskDetailActivity.class
        ).putExtra(TaskDetailActivity.EXTRA_TASK_ID, task.taskId);
        org.robolectric.android.controller.ActivityController<TaskDetailActivity> controller =
            Robolectric.buildActivity(TaskDetailActivity.class, detailIntent).create();
        TaskDetailActivity detail = controller.get();
        java.lang.reflect.Method detailRenderer = TaskDetailActivity.class.getDeclaredMethod(
            "render", TaskItem.class
        );
        detailRenderer.setAccessible(true);
        detailRenderer.invoke(detail, task);
        View detailRoot = detail.findViewById(android.R.id.content);
        assertNotNull(findText(detailRoot, "appointments.search, appointments.book"));
        assertNotNull(findText(detailRoot, "Confirm exact task"));
        assertNotNull(findText(detailRoot, "Decline"));
        controller.destroy();
    }

    @Test public void mainScreenExposesFirstClassChatAndTasksNavigation() {
        ensureWorkManager();
        MainActivity activity = Robolectric.buildActivity(MainActivity.class).create().get();
        View root = activity.findViewById(android.R.id.content);

        assertNotNull(findText(root, "Chat"));
        View tasks = findDescription(root, "Tasks");
        assertNotNull(tasks);
        tasks.performClick();
        Intent launched = shadowOf(activity).getNextStartedActivity();
        assertNotNull(launched);
        assertEquals(TasksActivity.class.getName(), launched.getComponent().getClassName());
    }

    private static void ensureWorkManager() {
        try {
            WorkManager.getInstance(RuntimeEnvironment.getApplication());
        } catch (IllegalStateException notInitialized) {
            WorkManager.initialize(
                RuntimeEnvironment.getApplication(),
                new Configuration.Builder().build()
            );
        }
    }

    @Test public void taskActivitiesArePrivateAndDeclared() throws Exception {
        PackageManager packageManager = RuntimeEnvironment.getApplication().getPackageManager();
        String packageName = RuntimeEnvironment.getApplication().getPackageName();
        ActivityInfo tasks = packageManager.getActivityInfo(
            new android.content.ComponentName(packageName, TasksActivity.class.getName()), 0
        );
        ActivityInfo detail = packageManager.getActivityInfo(
            new android.content.ComponentName(packageName, TaskDetailActivity.class.getName()), 0
        );
        assertFalse(tasks.exported);
        assertFalse(detail.exported);
    }

    @Test public void filtersUsePlainEnglishLabels() {
        assertEquals("Active", TasksActivity.filterLabel("ACTIVE"));
        assertEquals("Waiting", TasksActivity.filterLabel("WAITING_FOR_YOU"));
        assertEquals(
            "Waiting for you", TasksActivity.filterAccessibilityLabel("WAITING_FOR_YOU")
        );
        assertEquals("Problems", TasksActivity.filterLabel("PROBLEMS"));
    }

    @Test public void taskFilterSurvivesActivityRecreation() {
        TasksActivity first = Robolectric.buildActivity(TasksActivity.class).create().get();
        TextView waiting = findText(first.findViewById(android.R.id.content), "Waiting");
        assertNotNull(waiting);
        waiting.performClick();
        Bundle state = new Bundle();
        first.onSaveInstanceState(state);
        first.onDestroy();

        TasksActivity recreated = Robolectric.buildActivity(TasksActivity.class)
            .create(state).get();

        assertNotNull(findDescription(
            recreated.findViewById(android.R.id.content),
            "Show Waiting for you tasks, selected"
        ));
        recreated.onDestroy();
    }

    private static TextView findText(View root, String expected) {
        if (root instanceof TextView text && expected.contentEquals(text.getText())) return text;
        if (!(root instanceof ViewGroup group)) return null;
        for (int index = 0; index < group.getChildCount(); index++) {
            TextView found = findText(group.getChildAt(index), expected);
            if (found != null) return found;
        }
        return null;
    }

    private static JSONObject taskListResponse(String taskId, int current, int total)
        throws Exception {
        return new JSONObject()
            .put("counts", new JSONObject().put("ACTIVE", 1))
            .put("tasks", new JSONArray().put(new JSONObject()
                .put("task_id", taskId)
                .put("title", "Important-Only Inbox")
                .put("status", "RUNNING")
                .put("progress_current", current)
                .put("progress_total", total)
                .put("progress_unit", "messages")));
    }

    private static View findDescription(View root, String expected) {
        CharSequence description = root.getContentDescription();
        if (description != null && expected.contentEquals(description)) return root;
        if (!(root instanceof ViewGroup group)) return null;
        for (int index = 0; index < group.getChildCount(); index++) {
            View found = findDescription(group.getChildAt(index), expected);
            if (found != null) return found;
        }
        return null;
    }

    private static <T extends View> T findType(View root, Class<T> expected) {
        if (expected.isInstance(root)) return expected.cast(root);
        if (!(root instanceof ViewGroup group)) return null;
        for (int index = 0; index < group.getChildCount(); index++) {
            T found = findType(group.getChildAt(index), expected);
            if (found != null) return found;
        }
        return null;
    }

    private static java.util.List<String> flattenText(View root) {
        java.util.ArrayList<String> values = new java.util.ArrayList<>();
        if (root instanceof TextView text) values.add(text.getText().toString());
        if (root instanceof ViewGroup group) {
            for (int index = 0; index < group.getChildCount(); index++) {
                values.addAll(flattenText(group.getChildAt(index)));
            }
        }
        return values;
    }
}
