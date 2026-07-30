package com.aaron.jarvisvoice;

import java.util.concurrent.atomic.AtomicBoolean;

final class VoiceSessionState {
    private static final AtomicBoolean ACTIVE =
        new AtomicBoolean(false);

    private VoiceSessionState() {}

    static void setActive(boolean value) {
        ACTIVE.set(value);
    }

    static boolean isActive() {
        return ACTIVE.get();
    }
}
