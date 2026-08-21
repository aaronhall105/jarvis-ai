package com.aaron.jarvisvoice;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class InterruptionCommandPolicyTest {
    @Test public void explicitStopsAreCancellationOnly() {
        assertTrue(InterruptionCommandPolicy.isCancellationOnly("Jarvis, stop."));
        assertTrue(InterruptionCommandPolicy.isCancellationOnly("stop talking"));
        assertTrue(InterruptionCommandPolicy.isCancellationOnly("hold on please"));
        assertTrue(InterruptionCommandPolicy.isCancellationOnly("be quiet"));
    }

    @Test public void newQuestionsRemainNormalTurns() {
        assertFalse(InterruptionCommandPolicy.isCancellationOnly("what time is it"));
        assertFalse(InterruptionCommandPolicy.isCancellationOnly("stop the kitchen timer"));
        assertFalse(InterruptionCommandPolicy.isCancellationOnly("Jarvis turn off the lights"));
    }

    @Test public void repeatedInterruptionsRemainIdempotent() {
        assertTrue(InterruptionCommandPolicy.isCancellationOnly("stop"));
        assertTrue(InterruptionCommandPolicy.isCancellationOnly("stop"));
    }
}
