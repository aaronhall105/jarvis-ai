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
import android.widget.TextView;

import androidx.work.Configuration;
import androidx.work.WorkManager;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35)
public final class TaskCentreUiTest {
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
        assertEquals("0 / 46,502 messages", task.progressText());
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

    @Test public void mainScreenExposesFirstClassChatAndTasksNavigation() {
        WorkManager.initialize(
            RuntimeEnvironment.getApplication(),
            new Configuration.Builder().build()
        );
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
        assertEquals("Waiting for you", TasksActivity.filterLabel("WAITING_FOR_YOU"));
        assertEquals("Problems", TasksActivity.filterLabel("PROBLEMS"));
    }

    @Test public void taskFilterSurvivesActivityRecreation() {
        TasksActivity first = Robolectric.buildActivity(TasksActivity.class).create().get();
        TextView problems = findText(first.findViewById(android.R.id.content), "Problems");
        assertNotNull(problems);
        problems.performClick();
        Bundle state = new Bundle();
        first.onSaveInstanceState(state);
        first.onDestroy();

        TasksActivity recreated = Robolectric.buildActivity(TasksActivity.class)
            .create(state).get();

        assertNotNull(findDescription(
            recreated.findViewById(android.R.id.content),
            "Show Problems tasks, selected"
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
}
