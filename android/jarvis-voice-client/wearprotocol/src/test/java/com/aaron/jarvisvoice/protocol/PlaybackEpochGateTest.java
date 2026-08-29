package com.aaron.jarvisvoice.protocol;

import static org.junit.Assert.*;
import org.junit.Test;

public class PlaybackEpochGateTest {
    @Test public void turnCancellationRejectsQueuedAudioButKeepsSessionUsable() {
        PlaybackEpochGate gate = new PlaybackEpochGate();
        gate.begin(42L);
        long oldTurn = gate.snapshot(42L);
        assertTrue(gate.accepts(42L, oldTurn));

        gate.cancelTurn(42L);
        assertFalse(gate.accepts(42L, oldTurn));
        long newTurn = gate.snapshot(42L);
        assertTrue(newTurn >= 0L);
        assertTrue(gate.accepts(42L, newTurn));
    }

    @Test public void closedOrDifferentSessionRejectsAllFrames() {
        PlaybackEpochGate gate = new PlaybackEpochGate();
        gate.begin(7L);
        long epoch = gate.snapshot(7L);
        gate.cancelTurn(99L);
        assertTrue(gate.accepts(7L, epoch));
        gate.close();
        assertFalse(gate.accepts(7L, epoch));
        assertEquals(-1L, gate.snapshot(7L));
    }
}
