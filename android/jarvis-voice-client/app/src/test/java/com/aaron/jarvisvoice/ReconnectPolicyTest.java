package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class ReconnectPolicyTest {
    @Test public void delayGrowsAndRemainsBounded() {
        long first = ReconnectPolicy.delayMillis(0, 0);
        long third = ReconnectPolicy.delayMillis(3, 0);
        long maximum = ReconnectPolicy.delayMillis(99, 999999);

        assertEquals(500L, first);
        assertTrue(third >= 4_000L);
        assertTrue(maximum <= 20_000L);
    }

    @Test public void jitterIsDeterministicForDiagnostics() {
        assertEquals(
            ReconnectPolicy.delayMillis(2, 1234),
            ReconnectPolicy.delayMillis(2, 1234)
        );
    }
}
