package com.aaron.jarvisvoice;

import org.json.JSONObject;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;
import static org.junit.Assert.*;

@RunWith(RobolectricTestRunner.class)
@Config(manifest = Config.NONE, sdk = 35)
public class DevelopmentRequestStoreTest {
    @Before public void clearFixture() {
        RuntimeEnvironment.getApplication().getSharedPreferences("jarvis_supervised_development_requests_v1", 0)
            .edit().clear().commit();
    }

    @Test public void restartAndRetryReuseDurablyRecordedSubmission() throws Exception {
        DevelopmentRequestStore first = new DevelopmentRequestStore(RuntimeEnvironment.getApplication());
        JSONObject request = first.begin("Improve response latency");
        DevelopmentRequestStore restart = new DevelopmentRequestStore(RuntimeEnvironment.getApplication());
        assertEquals(request.toString(), restart.pending().toString());
        assertEquals(request.toString(), restart.begin("Improve response latency").toString());
        try { restart.begin("Replace with another request"); fail("uncertain request overwritten"); }
        catch (IllegalStateException expected) { }
        restart.acknowledge(new JSONObject().put("success", true).put("candidate_id", 78)
            .put("request_id", request.getString("request_id")));
        assertNull(first.pending());
        assertEquals(request.getString("request_id"), first.lastRequestId());
    }

    @Test public void mismatchedResponseCannotRetireAnUncertainRequest() throws Exception {
        DevelopmentRequestStore store = new DevelopmentRequestStore(RuntimeEnvironment.getApplication());
        JSONObject request = store.begin("Improve response latency");
        try {
            store.acknowledge(new JSONObject().put("success", true).put("candidate_id", 78)
                .put("request_id", "00000000-0000-4000-8000-000000000001"));
            fail("wrong request acknowledged");
        } catch (IllegalArgumentException expected) { }
        assertEquals(request.toString(), store.pending().toString());
    }
}
