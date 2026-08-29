package com.aaron.jarvisvoice.protocol;

/** Density-independent production layout policy shared by round Wear displays. */
public final class WearUiMetrics {
    private WearUiMetrics() {}

    public static int safeSideDp(int widthDp, boolean round) {
        if (!round) return 12;
        return Math.max(18, Math.min(24, widthDp / 12));
    }

    public static int actionTouchTargetDp() { return 44; }
    public static int textActionVisibleDp() { return 32; }
    public static int primaryActionVisibleDp() { return 34; }
}
