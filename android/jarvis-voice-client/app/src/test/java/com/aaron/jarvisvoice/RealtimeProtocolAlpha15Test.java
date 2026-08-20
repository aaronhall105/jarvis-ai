package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

public final class RealtimeProtocolAlpha15Test {
    @Test public void textCarriesClientTurnIdentity() throws Exception {
        JSONObject payload = new JSONObject(RealtimeProtocol.text("hello", true, 42L));
        assertEquals(42L, payload.getLong("client_turn_id"));
        assertEquals("19.0.0-alpha16", JarvisVersion.RELEASE);
    }

    @Test public void cancelCarriesCancelledTurnIdentity() throws Exception {
        JSONObject payload = new JSONObject(RealtimeProtocol.cancel(42L));
        assertEquals(42L, payload.getLong("client_turn_id"));
    }

    @Test public void serverTurnIdentityIsParsed() throws Exception {
        RealtimeProtocol.Event event = RealtimeProtocol.parse(
            "{\"type\":\"brain.response\",\"generation\":7,\"client_turn_id\":42}"
        );
        assertEquals(7, event.generation);
        assertEquals(42L, event.clientTurnId);
        assertTrue(event.success);
    }
}
