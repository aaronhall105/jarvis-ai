package com.aaron.jarvisvoice;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

public final class HomeAssistantTtsClientTest {
    @Test public void currentRunFailureCanTriggerFallback() throws Exception {
        JSONObject result = new JSONObject(
            "{\"id\":7,\"type\":\"result\",\"success\":false}"
        );
        assertTrue(HomeAssistantTtsClient.isFailureForActiveRun(result, 7));
    }

    @Test public void staleRunFailureCannotAffectNewSpeech() throws Exception {
        JSONObject result = new JSONObject(
            "{\"id\":6,\"type\":\"result\",\"success\":false}"
        );
        assertFalse(HomeAssistantTtsClient.isFailureForActiveRun(result, 7));
    }

    @Test public void successfulCurrentRunDoesNotTriggerFallback() throws Exception {
        JSONObject result = new JSONObject(
            "{\"id\":7,\"type\":\"result\",\"success\":true}"
        );
        assertFalse(HomeAssistantTtsClient.isFailureForActiveRun(result, 7));
    }
}
