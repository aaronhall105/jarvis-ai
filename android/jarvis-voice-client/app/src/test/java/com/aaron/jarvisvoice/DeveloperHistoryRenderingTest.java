package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import android.view.View;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.Map;
import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35)
public final class DeveloperHistoryRenderingTest {
    private MainActivity activity;
    private TextView status;

    @Before public void setUp() throws Exception {
        // Exercise the actual renderer without onCreate, services, networking,
        // preferences or credentials. All views belong to Robolectric's app.
        activity = Robolectric.buildActivity(MainActivity.class).get();
        status = new TextView(activity);
        LinearLayout messages = new LinearLayout(activity);
        ScrollView scroll = new ScrollView(activity);
        scroll.addView(messages);
        set("messageList", messages);
        set("messageScroll", scroll);
        set("statusText", status);
        set("emptyState", new View(activity));
        set("assistantMode", AssistantMode.DEVELOPER);
    }

    @Test public void restoredIdleThreadDoesNotClaimWorkIsRunning() throws Exception {
        restore("idle", new JSONArray());
        assertEquals("Ready", status.getText().toString());
        assertTrue(cards().isEmpty());
    }

    @Test public void repeatedFailureIdsKeepBothHistoricalExplanations() throws Exception {
        JSONArray turns = new JSONArray()
            .put(turn("first", item("failed", "First failure")))
            .put(turn("second", item("failed", "Second failure")));
        restore("completed", turns);
        assertEquals(2, cards().size());
        assertCard("Failed\nFirst failure");
        assertCard("Failed\nSecond failure");
        restore("completed", turns);
        assertEquals(2, cards().size());
    }

    @Test public void liveTurnsDoNotOverwriteEarlierFailureCards() throws Exception {
        start("first");
        event("item/completed", item("failed", "First failure"));
        start("second");
        event("item/completed", item("failed", "Second failure"));
        assertEquals(2, cards().size());
        assertCard("Failed\nFirst failure");
        assertCard("Failed\nSecond failure");
        // Retrying the same turn updates its card, not another turn's card.
        start("second");
        event("item/completed", item("paused", "Retry available"));
        assertEquals(2, cards().size());
        assertCard("Failed\nFirst failure");
        assertCard("Paused — retry available\nRetry available");
    }

    @Test public void liveCompletionAfterReconnectUpdatesOnlyTheRestoredTurn() throws Exception {
        restore("running", new JSONArray()
            .put(turn("first", item("failed", "First failure")))
            .put(turn("second", item("inProgress", "Reading"))));
        assertEquals("Running…", status.getText().toString());
        event("item/completed", item("completed", "Read finished"));
        assertEquals(2, cards().size());
        assertCard("Failed\nFirst failure");
        assertCard("Completed\nRead finished");
    }

    private static JSONObject item(String state, String message) throws Exception {
        return new JSONObject().put("id", "turn-error").put("type", "mcpToolCall")
            .put("status", state).put("message", message);
    }

    private static JSONObject turn(String id, JSONObject item) throws Exception {
        return new JSONObject().put("id", id).put("items", new JSONArray().put(item));
    }

    private void restore(String state, JSONArray turns) throws Exception {
        invoke("showDeveloperThreadHistory", new JSONObject().put("thread",
            new JSONObject().put("id", "api_fixture").put("status", state).put("turns", turns)));
    }

    private void start(String id) throws Exception {
        invoke("renderDeveloperEvent", new JSONObject().put("method", "turn/started")
            .put("params", new JSONObject().put("threadId", "api_fixture")
                .put("turn", new JSONObject().put("id", id))));
    }

    private void event(String method, JSONObject item) throws Exception {
        // API gateway tool events carry threadId, but need not carry turnId.
        invoke("renderDeveloperEvent", new JSONObject().put("method", method)
            .put("params", new JSONObject().put("threadId", "api_fixture").put("item", item)));
    }

    private void invoke(String name, JSONObject value) throws Exception {
        Method method = MainActivity.class.getDeclaredMethod(name, JSONObject.class);
        method.setAccessible(true);
        method.invoke(activity, value);
    }

    private void set(String name, Object value) throws Exception {
        Field field = MainActivity.class.getDeclaredField(name);
        field.setAccessible(true);
        field.set(activity, value);
    }

    @SuppressWarnings("unchecked")
    private Map<String, TextView> cards() throws Exception {
        Field field = MainActivity.class.getDeclaredField("developerActivityStatuses");
        field.setAccessible(true);
        return (Map<String, TextView>) field.get(activity);
    }

    private void assertCard(String text) throws Exception {
        assertTrue(cards().values().stream().anyMatch(view -> text.contentEquals(view.getText())));
    }
}
