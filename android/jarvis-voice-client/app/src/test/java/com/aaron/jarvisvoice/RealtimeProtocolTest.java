package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

public final class RealtimeProtocolTest {
    @Test public void authContainsProductVoiceSettingsAndNoApiKey() throws Exception {
        JSONObject auth = new JSONObject(RealtimeProtocol.auth(
            "mobile-token",
            "phone-1",
            "aaron",
            "Aaron",
            "cedar",
            "realtime",
            "live",
            "high",
            "mobile-chat-123"
        ));
        assertEquals("auth", auth.getString("type"));
        assertEquals("mobile-token", auth.getString("token"));
        assertEquals("aaron", auth.getString("user_id"));
        assertEquals("Aaron", auth.getString("user_name"));
        assertEquals("live", auth.getString("conversation_mode"));
        assertEquals("high", auth.getString("vad_eagerness"));
        assertEquals("mobile-chat-123", auth.getString("conversation_id"));
        assertFalse(auth.toString().contains("OPENAI_API_KEY"));
    }

    @Test public void brainDeltaAndResponseAreParsed() throws Exception {
        RealtimeProtocol.Event delta = RealtimeProtocol.parse(
            "{\"type\":\"brain.delta\",\"text\":\"Amber is\"}"
        );
        assertEquals("brain.delta", delta.type);
        assertEquals("Amber is", delta.text);

        RealtimeProtocol.Event response = RealtimeProtocol.parse(
            "{\"type\":\"brain.response\",\"text\":\"Amber is at home\",\"success\":true,\"conversation_id\":\"mobile-chat-123\"}"
        );
        assertEquals("Amber is at home", response.text);
        assertEquals("mobile-chat-123", response.conversationId);
        assertTrue(response.success);
    }

    @Test public void readyReportsConversationMode() throws Exception {
        RealtimeProtocol.Event event = RealtimeProtocol.parse(
            "{\"type\":\"ready\",\"model\":\"gpt-realtime\",\"voice\":\"marin\",\"voice_mode\":\"realtime\",\"conversation_mode\":\"standard\",\"unified_brain\":true}"
        );
        assertEquals("standard", event.conversationMode);
        assertTrue(event.unifiedBrain);
    }

    @Test public void textMessageCanDisableSpeech() throws Exception {
        JSONObject message = new JSONObject(RealtimeProtocol.text("hello", false));
        assertEquals("text", message.getString("type"));
        assertEquals("hello", message.getString("text"));
        assertFalse(message.getBoolean("speak"));
    }
}
