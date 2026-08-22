package com.aaron.jarvisvoice;

import com.aaron.jarvisvoice.protocol.VoiceEndpoint;

/** Determines whether an eight-second idle-listening deadline still owns the phone session. */
final class PhoneListeningTimeoutPolicy {
    static final long TIMEOUT_MS = 8_000L;

    private PhoneListeningTimeoutPolicy() {}

    static boolean shouldTimeout(
        boolean voiceActive,
        VoiceEndpoint endpoint,
        boolean brainActive,
        boolean playbackActive
    ) {
        return voiceActive
            && endpoint == VoiceEndpoint.PHONE
            && !brainActive
            && !playbackActive;
    }
}
