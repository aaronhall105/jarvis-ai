package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

public final class RealtimeProtocolTest {
    @Test public void authContainsVoiceModeAndNoApiKey() throws Exception {
        JSONObject auth = new JSONObject(RealtimeProtocol.auth(
            "mobile-token",
            "phone-1",
            "Aaron",
            "cedar",
            "realtime"
        ));
        assertEquals("auth", auth.getString("type"));
        assertEquals("mobile-token", auth.getString("token"));
        assertEquals("phone-1", auth.getString("device_id"));
        assertEquals("Aaron", auth.getString("user_name"));
        assertEquals("cedar", auth.getString("voice"));
        assertEquals("realtime", auth.getString("voice_mode"));
        assertTrue(!auth.toString().contains("OPENAI_API_KEY"));
    }

    @Test public void brainResponseIsParsed() throws Exception {
        RealtimeProtocol.Event event = RealtimeProtocol.parse(
            "{\"type\":\"brain.response\",\"text\":\"Amber is at home\",\"success\":true}"
        );
        assertEquals("brain.response", event.type);
        assertEquals("Amber is at home", event.text);
        assertTrue(event.success);
    }

    @Test public void readyReportsUnifiedBrain() throws Exception {
        RealtimeProtocol.Event event = RealtimeProtocol.parse(
            "{\"type\":\"ready\",\"model\":\"gpt-realtime\",\"voice\":\"marin\",\"voice_mode\":\"realtime\",\"unified_brain\":true}"
        );
        assertEquals("marin", event.voice);
        assertEquals("realtime", event.voiceMode);
        assertTrue(event.unifiedBrain);
    }

    @Test public void textMessageIsEncoded() throws Exception {
        JSONObject message = new JSONObject(RealtimeProtocol.text("hello"));
        assertEquals("text", message.getString("type"));
        assertEquals("hello", message.getString("text"));
        assertTrue(RealtimeProtocol.cancel().contains("cancel"));
    }
}
