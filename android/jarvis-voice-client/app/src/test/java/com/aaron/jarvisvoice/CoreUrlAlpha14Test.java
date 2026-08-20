package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

import org.junit.Test;

public final class CoreUrlAlpha14Test {
    @Test public void allowsPrivateLanCleartext() throws Exception {
        assertEquals(
            "ws://192.168.1.40:8000/api/realtime/voice",
            CoreUrl.websocket("http://192.168.1.40:8000")
        );
    }

    @Test public void allowsTailscaleCgnatCleartext() throws Exception {
        assertEquals(
            "ws://100.127.215.111:8000/api/realtime/voice",
            CoreUrl.websocket("http://100.127.215.111:8000")
        );
    }

    @Test public void requiresTlsForPublicHosts() {
        assertThrows(
            IllegalArgumentException.class,
            () -> CoreUrl.websocket("http://example.com:8000")
        );
    }

    @Test public void allowsPublicTls() throws Exception {
        assertEquals(
            "wss://example.com/api/realtime/voice",
            CoreUrl.websocket("https://example.com")
        );
    }

    @Test public void rejectsEmbeddedCredentials() {
        assertThrows(
            IllegalArgumentException.class,
            () -> CoreUrl.websocket("https://user:secret@example.com")
        );
    }
}
