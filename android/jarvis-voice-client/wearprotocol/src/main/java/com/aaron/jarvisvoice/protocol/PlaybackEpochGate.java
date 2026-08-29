package com.aaron.jarvisvoice.protocol;

/** Separates a long-lived endpoint session from cancellable playback turn epochs. */
public final class PlaybackEpochGate {
    private long sessionGeneration;
    private long epoch;

    public synchronized void begin(long generation) {
        epoch++;
        sessionGeneration = generation;
    }

    public synchronized void cancelTurn(long generation) {
        if (sessionGeneration == generation) epoch++;
    }

    public synchronized void close() {
        epoch++;
        sessionGeneration = 0L;
    }

    public synchronized long snapshot(long generation) {
        return sessionGeneration == generation ? epoch : -1L;
    }

    public synchronized boolean accepts(long generation, long acceptedEpoch) {
        return sessionGeneration == generation && epoch == acceptedEpoch;
    }
}
