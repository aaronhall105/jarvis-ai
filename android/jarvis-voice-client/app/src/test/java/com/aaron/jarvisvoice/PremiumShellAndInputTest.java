package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;
import static org.robolectric.Shadows.shadowOf;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputMethodManager;
import android.widget.EditText;
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
import org.robolectric.android.controller.ActivityController;
import org.robolectric.annotation.Config;
import org.robolectric.shadows.ShadowInputMethodManager;

import java.lang.reflect.Method;
import java.util.List;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35)
public final class PremiumShellAndInputTest {
    private SharedPreferences preferences;

    @Before public void setUp() {
        preferences = RuntimeEnvironment.getApplication().getSharedPreferences(
            "jarvis_voice_settings", Context.MODE_PRIVATE
        );
        preferences.edit().clear().putString("assistant_mode_v190210", "DEVELOPER").commit();
        ensureWorkManager();
    }

    @After public void tearDown() {
        preferences.edit().clear().commit();
        ShadowInputMethodManager.reset();
    }

    @Test public void homeChatAndTasksUseIdenticalSharedHeaderGeometry() {
        preferences.edit().putString("assistant_mode_v190210", "JARVIS").commit();
        ActivityController<HomeActivity> homeController = Robolectric.buildActivity(HomeActivity.class)
            .create().start().visible();
        ActivityController<MainActivity> chatController = Robolectric.buildActivity(MainActivity.class)
            .create().start().resume().visible();
        ActivityController<TasksActivity> tasksController = Robolectric.buildActivity(TasksActivity.class)
            .create().start().resume().visible();
        View chatHeader = findDescription(
            chatController.get().findViewById(android.R.id.content), "Jarvis app header"
        );
        View homeHeader = findDescription(
            homeController.get().findViewById(android.R.id.content), "Jarvis app header"
        );
        View taskHeader = findDescription(
            tasksController.get().findViewById(android.R.id.content), "Jarvis app header"
        );
        View chatNavigation = findDescription(chatHeader, "Primary navigation");
        View taskNavigation = findDescription(taskHeader, "Primary navigation");
        assertNotNull(homeHeader);
        assertNotNull(chatHeader);
        assertNotNull(taskHeader);

        for (int widthDp : new int[] {384, 320}) {
            int width = JarvisUi.dp(chatController.get(), widthDp);
            measureAtWidth(chatHeader, width);
            measureAtWidth(taskHeader, width);
            measureAtWidth(homeHeader, width);
            assertEquals(homeHeader.getMeasuredHeight(), chatHeader.getMeasuredHeight());
            assertEquals(chatHeader.getMeasuredWidth(), taskHeader.getMeasuredWidth());
            assertEquals(chatHeader.getMeasuredHeight(), taskHeader.getMeasuredHeight());
            assertEquals(chatNavigation.getMeasuredHeight(), taskNavigation.getMeasuredHeight());
            assertEquals(
                JarvisUi.dp(chatController.get(), JarvisUi.PRIMARY_NAV_HEIGHT),
                chatNavigation.getMeasuredHeight()
            );
            TextView chatWordmark = findText(chatHeader, "J A R V I S");
            TextView taskWordmark = findText(taskHeader, "J A R V I S");
            assertEquals(chatWordmark.getLeft(), taskWordmark.getLeft());
            assertEquals(chatWordmark.getTop(), taskWordmark.getTop());
            assertEquals(chatNavigation.getTop(), taskNavigation.getTop());
            assertEquals(chatNavigation.getPaddingLeft(), taskNavigation.getPaddingLeft());
            assertEquals(chatNavigation.getPaddingRight(), taskNavigation.getPaddingRight());
            assertEquals(
                JarvisUi.dp(chatController.get(), JarvisUi.PAGE_MARGIN),
                chatNavigation.getPaddingLeft()
            );
        }
        assertNotNull(findText(chatHeader, "J A R V I S"));
        assertNotNull(findText(homeHeader, "J A R V I S"));
        assertNotNull(findText(homeHeader, "Home"));
        assertNotNull(findText(homeHeader, "Chat"));
        assertNotNull(findText(homeHeader, "Tasks"));
        assertNotNull(findText(taskHeader, "J A R V I S"));
        assertNotNull(findDescription(chatHeader, "House activity"));
        assertNotNull(findDescription(taskHeader, "House activity"));
        for (String action : new String[] {"New chat", "Clear current chat", "Settings"}) {
            assertNotNull(findDescription(chatHeader, action));
            assertNotNull(findDescription(taskHeader, action));
        }
        assertEquals(
            JarvisUi.dp(chatController.get(), JarvisUi.TOUCH_TARGET),
            findDescription(chatHeader, "Settings").getMeasuredWidth()
        );
        homeController.destroy();
        chatController.destroy();
        tasksController.destroy();
    }

    @Test public void tapSendClearsTextRetainsFocusAndKeepsImeVisible() {
        ActivityController<MainActivity> controller = createChat();
        MainActivity activity = controller.get();
        EditText input = findType(activity.findViewById(android.R.id.content), EditText.class);
        View send = findDescription(activity.findViewById(android.R.id.content), "Send message");
        assertNotNull(input);
        assertNotNull(send);
        input.requestFocus();
        InputMethodManager keyboard = (InputMethodManager) activity.getSystemService(
            Context.INPUT_METHOD_SERVICE
        );
        keyboard.showSoftInput(input, InputMethodManager.SHOW_IMPLICIT);
        input.setText("Keep the composer ready");

        send.performClick();
        shadowOf(android.os.Looper.getMainLooper()).idle();

        assertEquals("", input.getText().toString());
        assertTrue(input.hasFocus());
        assertTrue(shadowOf(keyboard).isSoftInputVisible());

        controller.destroy();
    }

    @Test public void imeSendMatchesTapSendAndStreamingDoesNotStealFocus() throws Exception {
        ActivityController<MainActivity> controller = createChat();
        MainActivity activity = controller.get();
        EditText input = findType(activity.findViewById(android.R.id.content), EditText.class);
        InputMethodManager keyboard = (InputMethodManager) activity.getSystemService(
            Context.INPUT_METHOD_SERVICE
        );
        input.requestFocus();
        keyboard.showSoftInput(input, InputMethodManager.SHOW_IMPLICIT);
        input.setText("Send from the keyboard");

        input.onEditorAction(EditorInfo.IME_ACTION_SEND);
        shadowOf(android.os.Looper.getMainLooper()).idle();
        assertEquals("", input.getText().toString());
        assertTrue(input.hasFocus());
        assertTrue(shadowOf(keyboard).isSoftInputVisible());

        input.setText("The next message can be typed now");
        Method append = MainActivity.class.getDeclaredMethod("appendStreaming", String.class);
        append.setAccessible(true);
        append.invoke(activity, "Assistant response");
        shadowOf(android.os.Looper.getMainLooper()).idle();
        assertEquals("The next message can be typed now", input.getText().toString());
        assertTrue(input.hasFocus());
        assertTrue(shadowOf(keyboard).isSoftInputVisible());

        Method addMessage = MainActivity.class.getDeclaredMethod(
            "addMessageView", ChatMessage.class, boolean.class
        );
        addMessage.setAccessible(true);
        addMessage.invoke(
            activity,
            new ChatMessage(ChatMessage.ASSISTANT, "A completed response", System.currentTimeMillis()),
            true
        );
        shadowOf(android.os.Looper.getMainLooper()).idle();
        assertEquals("The next message can be typed now", input.getText().toString());
        assertTrue(input.hasFocus());
        assertTrue(shadowOf(keyboard).isSoftInputVisible());

        // Android Back dismisses the IME outside the Activity. Once dismissed,
        // later assistant output must honour that user choice and not reopen it.
        keyboard.hideSoftInputFromWindow(input.getWindowToken(), 0);
        append.invoke(activity, " continues");
        shadowOf(android.os.Looper.getMainLooper()).idle();
        assertFalse(shadowOf(keyboard).isSoftInputVisible());
        controller.destroy();
    }

    @Test public void voiceEntryDismissesImeAndFreshChatDoesNotForceItOpen() {
        ActivityController<MainActivity> controller = createChat();
        MainActivity activity = controller.get();
        EditText input = findType(activity.findViewById(android.R.id.content), EditText.class);
        InputMethodManager keyboard = (InputMethodManager) activity.getSystemService(
            Context.INPUT_METHOD_SERVICE
        );
        input.requestFocus();
        keyboard.showSoftInput(input, InputMethodManager.SHOW_IMPLICIT);

        View microphone = findDescription(
            activity.findViewById(android.R.id.content), "Dictate developer instruction"
        );
        assertNotNull(microphone);
        microphone.performClick();
        assertFalse(input.hasFocus());
        assertFalse(shadowOf(keyboard).isSoftInputVisible());
        controller.destroy();

        ActivityController<MainActivity> freshController = createChat();
        MainActivity fresh = freshController.get();
        EditText freshInput = findType(fresh.findViewById(android.R.id.content), EditText.class);
        InputMethodManager freshKeyboard = (InputMethodManager) fresh.getSystemService(
            Context.INPUT_METHOD_SERVICE
        );
        assertFalse(shadowOf(freshKeyboard).isSoftInputVisible());
        assertFalse(freshInput.hasFocus());
        freshController.destroy();
    }

    @Test public void switchingToTasksDismissesImeWithoutResettingAppState() {
        ActivityController<MainActivity> controller = createChat();
        MainActivity activity = controller.get();
        EditText input = findType(activity.findViewById(android.R.id.content), EditText.class);
        InputMethodManager keyboard = (InputMethodManager) activity.getSystemService(
            Context.INPUT_METHOD_SERVICE
        );
        input.requestFocus();
        keyboard.showSoftInput(input, InputMethodManager.SHOW_IMPLICIT);
        View tasks = findDescription(activity.findViewById(android.R.id.content), "Tasks");
        assertNotNull(tasks);

        tasks.performClick();

        assertFalse(input.hasFocus());
        assertFalse(shadowOf(keyboard).isSoftInputVisible());
        Intent launched = shadowOf(activity).getNextStartedActivity();
        assertEquals(TasksActivity.class.getName(), launched.getComponent().getClassName());
        controller.destroy();
    }

    @Test public void smartInboxCardUsesPremiumProductCopyWithoutDuplicateSyncOrMetrics()
        throws Exception {
        TaskItem task = TaskItem.fromJson(smartInbox());
        TasksActivity activity = Robolectric.buildActivity(TasksActivity.class).create().get();
        Method cardFactory = TasksActivity.class.getDeclaredMethod(
            "card", TaskItem.class, boolean.class
        );
        cardFactory.setAccessible(true);
        View card = (View) cardFactory.invoke(activity, task, false);
        List<String> cardText = flattenText(card);

        assertTrue(cardText.contains("Smart Inbox"));
        assertTrue(cardText.contains("Keeps your inbox focused automatically"));
        assertTrue(cardText.contains("Monitoring Gmail and Outlook"));
        assertTrue(cardText.contains("Initial cleanup complete"));
        assertTrue(cardText.contains("48,563 reviewed · 7,396 removed"));
        assertTrue(cardText.contains("10,876 reviewed · 6,339 moved to Bin"));
        assertTrue(cardText.contains("37,687 reviewed · 1,057 moved to Deleted Items"));
        assertTrue(cardText.contains("View details"));
        assertTrue(cardText.contains("›"));
        assertTrue(cardText.stream().noneMatch(value -> value.contains("Important-Only Inbox")));
        assertTrue(cardText.stream().noneMatch(value -> value.startsWith("Synced ")));
        assertEquals(1L, cardText.stream().filter(value -> value.contains("7,396")).count());

        Method renderer = TasksActivity.class.getDeclaredMethod(
            "render", List.class, JSONObject.class, boolean.class, long.class
        );
        renderer.setAccessible(true);
        renderer.invoke(
            activity,
            List.of(task),
            new JSONObject().put("ACTIVE", 1),
            false,
            System.currentTimeMillis()
        );
        List<String> screenText = flattenText(activity.findViewById(android.R.id.content));
        assertEquals(1L, screenText.stream().filter(value -> value.startsWith("Synced ")).count());
        activity.onDestroy();
    }

    private static ActivityController<MainActivity> createChat() {
        return Robolectric.buildActivity(MainActivity.class).create().start().resume().visible();
    }

    private static JSONObject smartInbox() throws Exception {
        return new JSONObject()
            .put("task_id", "important_only:inbox")
            .put("title", "Important-Only Inbox")
            .put("status", "MONITORING")
            .put("work_mode", "CONTINUOUS")
            .put("progress", new JSONObject().put("mode", "NONE"))
            .put("backlog", new JSONObject()
                .put("status", "COMPLETED")
                .put("title", "Initial cleanup")
                .put("summary", "48,563 reviewed · 7,396 removed")
                .put("reviewed_count", 48_563)
                .put("moved_count", 7_396))
            .put("subtasks", new JSONArray()
                .put(provider("Gmail", 10_876, 6_339, "Bin"))
                .put(provider("Outlook", 37_687, 1_057, "Deleted Items")));
    }

    private static JSONObject provider(
        String title, long reviewed, long moved, String destination
    ) throws Exception {
        return new JSONObject()
            .put("title", title)
            .put("status", "monitoring")
            .put("progress", new JSONObject().put("mode", "NONE"))
            .put("backlog", new JSONObject()
                .put("status", "COMPLETED")
                .put("reviewed_count", reviewed)
                .put("moved_count", moved))
            .put("metrics", new JSONArray().put(new JSONObject()
                .put("primary", true)
                .put("value", moved)
                .put("destination", destination)));
    }

    private static void measureAtWidth(View view, int width) {
        view.measure(
            View.MeasureSpec.makeMeasureSpec(width, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED)
        );
        view.layout(0, 0, width, view.getMeasuredHeight());
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
        if (root == null) return null;
        CharSequence description = root.getContentDescription();
        if (description != null && expected.contentEquals(description)) return root;
        if (!(root instanceof ViewGroup group)) return null;
        for (int index = 0; index < group.getChildCount(); index++) {
            View found = findDescription(group.getChildAt(index), expected);
            if (found != null) return found;
        }
        return null;
    }

    private static <T extends View> T findType(View root, Class<T> type) {
        if (type.isInstance(root)) return type.cast(root);
        if (!(root instanceof ViewGroup group)) return null;
        for (int index = 0; index < group.getChildCount(); index++) {
            T found = findType(group.getChildAt(index), type);
            if (found != null) return found;
        }
        return null;
    }

    private static List<String> flattenText(View root) {
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
