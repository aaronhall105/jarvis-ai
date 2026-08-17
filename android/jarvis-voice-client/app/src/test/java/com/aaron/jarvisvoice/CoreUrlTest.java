package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

import org.junit.Test;

public final class CoreUrlTest {
    @Test public void localHttpBecomesWebSocket() throws Exception {
        assertEquals("ws://192.168.1.40:8000/api/realtime/voice", CoreUrl.websocket("http://192.168.1.40:8000"));
    }

    @Test public void secureTailscaleUrlBecomesSecureWebSocket() throws Exception {
        assertEquals("wss://jarvis.example.ts.net/api/realtime/voice", CoreUrl.websocket("https://jarvis.example.ts.net/"));
    }

    @Test public void endpointIsNotDuplicated() throws Exception {
        assertEquals(
            "ws://192.168.1.40:8000/api/realtime/voice",
            CoreUrl.websocket(
                "ws://192.168.1.40:8000/api/realtime/voice"
            )
        );
    }

    @Test public void unsupportedSchemeFails() {
        assertThrows(IllegalArgumentException.class, () -> CoreUrl.websocket("ftp://host"));
    }
}
