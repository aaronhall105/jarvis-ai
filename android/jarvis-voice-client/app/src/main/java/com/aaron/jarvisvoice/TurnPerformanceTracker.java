package com.aaron.jarvisvoice;

import java.util.Arrays;

public final class TurnPerformanceTracker {
    public interface Clock {
        long nowMillis();
    }

    public interface Sink {
        void accept(Snapshot snapshot);
    }

    public static final class Snapshot {
        public final long brainStartMs;
        public final long firstTokenMs;
        public final long firstAudioMs;
        public final long totalMs;
        public final int sampleCount;
        public final long medianTotalMs;
        public final long worstTotalMs;
        public final int droppedThisTurn;
        public final int droppedTotal;

        Snapshot(
            long brainStartMs,
            long firstTokenMs,
            long firstAudioMs,
            long totalMs,
            int sampleCount,
            long medianTotalMs,
            long worstTotalMs,
            int droppedThisTurn,
            int droppedTotal
        ) {
            this.brainStartMs = brainStartMs;
            this.firstTokenMs = firstTokenMs;
            this.firstAudioMs = firstAudioMs;
            this.totalMs = totalMs;
            this.sampleCount = sampleCount;
            this.medianTotalMs = medianTotalMs;
            this.worstTotalMs = worstTotalMs;
            this.droppedThisTurn = droppedThisTurn;
            this.droppedTotal = droppedTotal;
        }
    }

    private static final int WINDOW = 20;

    private final Clock clock;
    private final Sink sink;
    private final long[] totals = new long[WINDOW];

    private int totalCount;
    private int droppedTotal;
    private int droppedThisTurn;
    private long startedAt = -1L;
    private long brainAt = -1L;
    private long firstTokenAt = -1L;
    private long firstAudioAt = -1L;

    public TurnPerformanceTracker(VoiceDiagnosticsStore diagnostics) {
        this(
            () -> System.nanoTime() / 1_000_000L,
            diagnostics::recordTurnPerformance
        );
    }

    TurnPerformanceTracker(Clock clock, Sink sink) {
        this.clock = clock;
        this.sink = sink;
    }

    public void beginTurn() {
        if (startedAt >= 0L) return;
        startedAt = clock.nowMillis();
        brainAt = -1L;
        firstTokenAt = -1L;
        firstAudioAt = -1L;
        droppedThisTurn = 0;
    }

    public void markBrainStarted() {
        ensureStarted();
        if (brainAt < 0L) brainAt = clock.nowMillis();
    }

    public void markFirstToken() {
        ensureStarted();
        if (firstTokenAt < 0L) firstTokenAt = clock.nowMillis();
    }

    public void markFirstAudio() {
        ensureStarted();
        if (firstAudioAt < 0L) firstAudioAt = clock.nowMillis();
    }

    public void recordDroppedAudioFrame() {
        droppedThisTurn++;
        droppedTotal++;
    }

    public void finishTurn() {
        if (startedAt < 0L) return;
        long now = clock.nowMillis();
        long total = Math.max(0L, now - startedAt);
        totals[totalCount % WINDOW] = total;
        totalCount++;

        int samples = Math.min(totalCount, WINDOW);
        long[] window = Arrays.copyOf(totals, samples);
        Arrays.sort(window);
        long median = samples == 0
            ? -1L
            : window[(samples - 1) / 2];
        long worst = samples == 0 ? -1L : window[samples - 1];

        sink.accept(new Snapshot(
            elapsed(brainAt),
            elapsed(firstTokenAt),
            elapsed(firstAudioAt),
            total,
            samples,
            median,
            worst,
            droppedThisTurn,
            droppedTotal
        ));
        resetActive();
    }

    public void abandonTurn() {
        resetActive();
    }

    private void ensureStarted() {
        if (startedAt < 0L) beginTurn();
    }

    private long elapsed(long eventAt) {
        return eventAt < 0L || startedAt < 0L
            ? -1L
            : Math.max(0L, eventAt - startedAt);
    }

    private void resetActive() {
        startedAt = -1L;
        brainAt = -1L;
        firstTokenAt = -1L;
        firstAudioAt = -1L;
        droppedThisTurn = 0;
    }
}
