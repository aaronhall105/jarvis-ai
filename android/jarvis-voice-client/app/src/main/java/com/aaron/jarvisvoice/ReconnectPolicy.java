package com.aaron.jarvisvoice;

/** Deterministic reconnect policy with bounded exponential backoff and jitter. */
public final class ReconnectPolicy {
    private ReconnectPolicy() {}

    public static long delayMillis(int attempt, int jitterSeed) {
        int boundedAttempt = Math.max(0, Math.min(7, attempt));
        long base = Math.min(16_000L, 500L * (1L << boundedAttempt));
        long range = Math.max(1L, base / 4L);
        long jitter = Math.floorMod(jitterSeed, (int) range);
        return Math.min(20_000L, base + jitter);
    }
}
