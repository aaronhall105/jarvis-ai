package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;

import java.util.ArrayList;
import java.util.List;

import org.junit.Test;

public final class TurnPerformanceTrackerTest {
    @Test public void recordsLatencyAndRollingSummary() {
        MutableClock clock = new MutableClock();
        List<TurnPerformanceTracker.Snapshot> values =
            new ArrayList<>();
        TurnPerformanceTracker tracker =
            new TurnPerformanceTracker(clock, values::add);

        tracker.beginTurn();
        clock.advance(40);
        tracker.markBrainStarted();
        clock.advance(30);
        tracker.markFirstToken();
        clock.advance(50);
        tracker.markFirstAudio();
        tracker.recordDroppedAudioFrame();
        clock.advance(80);
        tracker.finishTurn();

        TurnPerformanceTracker.Snapshot result = values.get(0);
        assertEquals(40L, result.brainStartMs);
        assertEquals(70L, result.firstTokenMs);
        assertEquals(120L, result.firstAudioMs);
        assertEquals(200L, result.totalMs);
        assertEquals(1, result.sampleCount);
        assertEquals(200L, result.medianTotalMs);
        assertEquals(200L, result.worstTotalMs);
        assertEquals(1, result.droppedThisTurn);
        assertEquals(1, result.droppedTotal);
    }

    @Test public void duplicateBeginDoesNotResetTurn() {
        MutableClock clock = new MutableClock();
        List<TurnPerformanceTracker.Snapshot> values =
            new ArrayList<>();
        TurnPerformanceTracker tracker =
            new TurnPerformanceTracker(clock, values::add);

        tracker.beginTurn();
        clock.advance(50);
        tracker.beginTurn();
        clock.advance(50);
        tracker.finishTurn();

        assertEquals(100L, values.get(0).totalMs);
    }

    private static final class MutableClock
        implements TurnPerformanceTracker.Clock {
        private long value;

        @Override public long nowMillis() {
            return value;
        }

        void advance(long milliseconds) {
            value += milliseconds;
        }
    }
}
