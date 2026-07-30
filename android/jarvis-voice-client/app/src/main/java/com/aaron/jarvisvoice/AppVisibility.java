package com.aaron.jarvisvoice;

import java.util.concurrent.atomic.AtomicInteger;

final class AppVisibility {
    private static final AtomicInteger STARTED_ACTIVITIES =
        new AtomicInteger(0);

    private AppVisibility() {}

    static void activityStarted() {
        STARTED_ACTIVITIES.incrementAndGet();
    }

    static void activityStopped() {
        STARTED_ACTIVITIES.updateAndGet(
            current -> Math.max(0, current - 1)
        );
    }

    static boolean isVisible() {
        return STARTED_ACTIVITIES.get() > 0;
    }
}
