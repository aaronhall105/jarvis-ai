package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

public final class RealtimeProtocolAlpha5_1Test {
    @Test public void authCarriesTrustedLondonTime() throws Exception {
        JSONObject payload = new JSONObject(RealtimeProtocol.auth(
            "token",
            "device",
            "aaron",
            "Aaron",
            "marin",
            "realtime",
            "standard",
            "high",
            "conversation"
        ));

        assertTrue(
            payload.getString("client_release")
                .startsWith("19.0.0-alpha")
        );
        assertEquals("Europe/London", payload.getString("timezone"));
        assertEquals("websocket_pcm", payload.getString("transport"));
        assertTrue(payload.getString("local_datetime").contains("T"));
    }

    @Test public void textRefreshesLocalTimeContext() throws Exception {
        JSONObject payload = new JSONObject(
            RealtimeProtocol.text("What time is it?", true)
        );
        assertEquals("Europe/London", payload.getString("timezone"));
        assertTrue(payload.has("utc_offset_seconds"));
        assertTrue(payload.getString("local_datetime").contains("T"));
    }

    @Test public void endpointHealthUrlsAreStable() {
        assertEquals(
            "http://192.168.1.40:8000/health",
            CoreEndpointSelector.healthUrl("http://192.168.1.40:8000/")
        );
        assertEquals(
            "http://100.127.215.111:8000",
            CoreEndpointSelector.DEFAULT_TAILSCALE_URL
        );
    }
}
