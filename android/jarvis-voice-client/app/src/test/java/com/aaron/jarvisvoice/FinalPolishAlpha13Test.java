package com.aaron.jarvisvoice;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class FinalPolishAlpha13Test {
    @Test public void wakeRetriesAreBoundedAndOrdered() {
        assertArrayEquals(
            new long[] {0L, 250L, 1_000L, 3_000L},
            WakeRecoveryPolicy.retryDelaysMs()
        );
    }

    @Test public void stoppedWakeStartsOutsideConversation() {
        assertTrue(
            WakeRecoveryPolicy.shouldStart(
                false, false, true, true, false
            )
        );
    }

    @Test public void activeVoiceOwnsTheMicrophone() {
        assertFalse(
            WakeRecoveryPolicy.shouldStart(
                false, true, true, true, false
            )
        );
    }

    @Test public void runningWakeIsNotRestarted() {
        assertFalse(
            WakeRecoveryPolicy.shouldStart(
                false, false, true, true, true
            )
        );
    }
}
