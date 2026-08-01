package com.aaron.jarvisvoice;

public final class WakeRecoveryPolicy {
    private static final long[] RETRY_DELAYS_MS = {
        0L,
        250L,
        1_000L,
        3_000L
    };

    private WakeRecoveryPolicy() {}

    public static long[] retryDelaysMs() {
        return RETRY_DELAYS_MS.clone();
    }

    public static boolean shouldWatch(
        boolean stopping,
        boolean voiceActive,
        boolean wakeEnabled,
        boolean microphoneGranted
    ) {
        return !stopping
            && !voiceActive
            && wakeEnabled
            && microphoneGranted;
    }

    public static boolean shouldStart(
        boolean stopping,
        boolean voiceActive,
        boolean wakeEnabled,
        boolean microphoneGranted,
        boolean engineRunning
    ) {
        return shouldWatch(
            stopping,
            voiceActive,
            wakeEnabled,
            microphoneGranted
        ) && !engineRunning;
    }
}
