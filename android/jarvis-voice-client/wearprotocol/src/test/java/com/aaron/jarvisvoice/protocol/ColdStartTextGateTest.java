package com.aaron.jarvisvoice.protocol;

import static org.junit.Assert.*;
import org.junit.Test;

public class ColdStartTextGateTest {
    @Test public void firstTypedTurnWaitsForCoreReadyThenDrainsOnce() {
        ColdStartTextGate gate = new ColdStartTextGate(); gate.begin(12L);
        assertFalse(gate.offer(12L, "What time is it?"));
        assertEquals("What time is it?", gate.markReady(12L));
        assertEquals("", gate.markReady(12L));
        assertTrue(gate.offer(12L, "And the day?"));
    }
    @Test public void staleGenerationCannotLeakTypedTurn() {
        ColdStartTextGate gate = new ColdStartTextGate(); gate.begin(12L);
        gate.offer(12L, "stale"); gate.begin(13L);
        assertEquals("", gate.markReady(12L));
        assertEquals("", gate.markReady(13L));
    }
}
