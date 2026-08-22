package com.aaron.jarvisvoice.protocol;

/** Single source of truth for watch microphone ownership. */
public final class WatchMicrophonePolicy {
    private WatchMicrophonePolicy() {}

    public static boolean shouldCapture(WatchConversationState state) {
        return state == WatchConversationState.LISTENING
            || state == WatchConversationState.FOLLOW_UP;
    }
}
