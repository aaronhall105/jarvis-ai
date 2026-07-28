package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

public final class RealtimeProtocolTest {
    @Test public void authContainsDeviceAndUserWithoutApiKey() throws Exception {
        JSONObject auth = new JSONObject(RealtimeProtocol.auth("mobile-token", "phone-1", "Aaron"));
        assertEquals("auth", auth.getString("type"));
        assertEquals("mobile-token", auth.getString("token"));
        assertEquals("phone-1", auth.getString("device_id"));
        assertEquals("Aaron", auth.getString("user_name"));
    }

    @Test public void toolCommandIsParsed() throws Exception {
        RealtimeProtocol.Event event = RealtimeProtocol.parse(
            "{\"type\":\"tool.started\",\"command\":\"Turn the light off\"}"
        );
        assertEquals("tool.started", event.type);
        assertEquals("Turn the light off", event.command);
    }

    @Test public void textMessageIsEncoded() throws Exception {
        JSONObject message = new JSONObject(RealtimeProtocol.text("hello"));
        assertEquals("text", message.getString("type"));
        assertEquals("hello", message.getString("text"));
        assertTrue(RealtimeProtocol.cancel().contains("cancel"));
    }
}
