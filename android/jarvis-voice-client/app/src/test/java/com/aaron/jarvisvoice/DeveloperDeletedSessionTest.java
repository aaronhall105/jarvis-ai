package com.aaron.jarvisvoice;

import static org.junit.Assert.*;
import android.os.Looper;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import okhttp3.Request;
import okhttp3.WebSocket;
import okio.ByteString;
import org.json.JSONObject;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.Shadows;
import org.robolectric.annotation.Config;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35)
public final class DeveloperDeletedSessionTest {
    private DeveloperClient client;
    private final Socket socket = new Socket();
    private final List<JSONObject> events = new ArrayList<>();
    private final List<String> states = new ArrayList<>();

    @Before public void setup() throws Exception {
        client = new DeveloperClient(RuntimeEnvironment.getApplication(), new DeveloperClient.Listener() {
            public void onState(String value) { states.add(value); }
            public void onEvent(JSONObject value) { events.add(value); }
            public void onError(String value) { }
        });
        set("socket", socket);
        set("authenticated", true);
        set("token", "inert-fixture");
        client.selectThread("api_deleted");
    }
    @After public void finish() { client.destroy(); }

    @Test public void confirmedMissingSelectionClearsOnlySelectionAndKeepsSocket() throws Exception {
        set("pendingInstruction", "queued prior task");
        handle(error(1, "THREAD_NOT_FOUND", "api_deleted"));
        assertEquals("", client.threadId());
        assertEquals("inert-fixture", get("token"));
        assertEquals(true, get("authenticated"));
        assertEquals(true, get("sessionReady"));
        assertEquals(false, get("reconnectScheduled"));
        assertFalse(socket.closed);
        assertEquals("thread.unavailable", events.get(0).optString("type"));
        assertTrue(states.stream().noneMatch("Reconnecting…"::equals));
        // Even a later auth acknowledgement cannot move queued work to a new chat.
        handle(new JSONObject().put("type", "auth.ok"));
        assertTrue(socket.sent.stream().noneMatch(v -> v.contains("thread.start") || v.contains("turn.start")));
        client.sendInstruction("new explicit task");
        JSONObject start = new JSONObject(socket.sent.get(socket.sent.size()-1));
        assertEquals("thread.start", start.getString("type"));
        handle(new JSONObject().put("type", "response").put("request_id", start.getLong("request_id"))
            .put("result", new JSONObject().put("thread", new JSONObject().put("id", "api_new"))));
        assertTrue(socket.sent.get(socket.sent.size()-1).contains("new explicit task"));
        assertFalse(socket.sent.get(socket.sent.size()-1).contains("queued prior task"));
    }

    @Test public void unknownLegacyOrMismatchedErrorCannotClearSelection() throws Exception {
        for (JSONObject error : List.of(
                error(999, "THREAD_NOT_FOUND", "api_deleted"),
                error(1, "THREAD_NOT_FOUND", "api_other"),
                error(1, "REQUEST_FAILED", "api_deleted"),
                new JSONObject().put("type", "error").put("message", "Unknown API thread"))) {
            pending(1, "api_deleted");
            handle(error);
            assertEquals("api_deleted", client.threadId());
            assertFalse(socket.closed);
            assertEquals(false, get("reconnectScheduled"));
        }
        assertTrue(events.isEmpty());
    }

    @Test public void delayedMissingResponseCannotClearNewerSelection() throws Exception {
        client.selectThread("api_kept");
        handle(error(1, "THREAD_NOT_FOUND", "api_deleted"));
        assertEquals("api_kept", client.threadId());
        assertTrue(events.isEmpty());
        handle(new JSONObject().put("type", "response").put("request_id", 2)
            .put("result", new JSONObject().put("thread", new JSONObject().put("id", "api_kept"))));
        assertEquals(true, get("sessionReady"));
        assertEquals("thread.resume", events.get(0).getString("request_kind"));
    }

    @Test public void oldConnectionAndWrongThreadSuccessCannotRestoreStaleSelection() throws Exception {
        handle(new JSONObject().put("type", "response").put("request_id", 1)
            .put("result", new JSONObject().put("thread", new JSONObject().put("id", "api_other"))));
        assertEquals("api_deleted", client.threadId());
        assertEquals(false, get("sessionReady"));
        client.newSession();
        handle(new JSONObject().put("type", "response").put("request_id", 1)
            .put("result", new JSONObject().put("thread", new JSONObject().put("id", "api_deleted"))));
        assertEquals("", client.threadId());
        assertTrue(events.isEmpty());
    }

    private JSONObject error(long id, String code, String thread) throws Exception {
        return new JSONObject().put("type", "error").put("request_id", id)
            .put("code", code).put("thread_id", thread).put("message", "Conversation unavailable");
    }
    @SuppressWarnings("unchecked") private void pending(long id, String thread) throws Exception {
        ((Map<Long,String>)get("requestKinds")).put(id, "thread.resume");
        ((Map<Long,String>)get("resumeThreads")).put(id, thread);
    }
    private Object get(String name) throws Exception {
        Field f = DeveloperClient.class.getDeclaredField(name); f.setAccessible(true); return f.get(client);
    }
    private void set(String name, Object value) throws Exception {
        Field f = DeveloperClient.class.getDeclaredField(name); f.setAccessible(true); f.set(client, value);
    }
    private void handle(JSONObject value) throws Exception {
        Method method = DeveloperClient.class.getDeclaredMethod("handle", JSONObject.class);
        method.setAccessible(true); method.invoke(client, value);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
    }
    private static final class Socket implements WebSocket {
        final List<String> sent = new ArrayList<>(); boolean closed;
        public Request request() { return new Request.Builder().url("http://localhost").build(); }
        public long queueSize() { return 0; }
        public boolean send(String text) { sent.add(text); return true; }
        public boolean send(ByteString data) { throw new AssertionError("Unexpected binary send"); }
        public boolean close(int code, String reason) { closed = true; return true; }
        public void cancel() { closed = true; }
    }
}
