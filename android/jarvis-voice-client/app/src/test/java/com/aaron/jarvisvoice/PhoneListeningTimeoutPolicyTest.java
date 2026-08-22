package com.aaron.jarvisvoice;

import static org.junit.Assert.*;
import com.aaron.jarvisvoice.protocol.VoiceEndpoint;
import org.junit.Test;

public class PhoneListeningTimeoutPolicyTest {
    @Test public void phoneIdleListeningTimesOutAfterEightSeconds() {
        assertEquals(8_000L, PhoneListeningTimeoutPolicy.TIMEOUT_MS);
        assertTrue(PhoneListeningTimeoutPolicy.shouldTimeout(
            true, VoiceEndpoint.PHONE, false, false));
    }

    @Test public void activeTurnPlaybackAndWatchAreNeverClosedByPhoneTimer() {
        assertFalse(PhoneListeningTimeoutPolicy.shouldTimeout(
            true, VoiceEndpoint.PHONE, true, false));
        assertFalse(PhoneListeningTimeoutPolicy.shouldTimeout(
            true, VoiceEndpoint.PHONE, false, true));
        assertFalse(PhoneListeningTimeoutPolicy.shouldTimeout(
            true, VoiceEndpoint.WATCH, false, false));
    }
}
