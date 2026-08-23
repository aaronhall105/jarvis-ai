package com.aaron.jarvisvoice;

import com.aaron.jarvisvoice.protocol.VoiceEndpoint;

/**
 * Owns the phone-only eight-second idle-listening deadline.
 *
 * A voice session existing is not enough to own a deadline.
 * The active microphone or recogniser must actually be ready
 * to capture speech.
 */
final class PhoneListeningTimeoutPolicy {

    static final long TIMEOUT_MS = 8_000L;

    private PhoneListeningTimeoutPolicy() {}

    static boolean isMeaningfulTranscript(
        String text
    ) {
        return text != null
            && !text.isBlank();
    }

    static boolean shouldArm(
        boolean voiceActive,
        VoiceEndpoint endpoint,
        boolean brainActive,
        boolean playbackActive,
        boolean captureReady
    ) {
        return shouldTimeout(
            voiceActive,
            endpoint,
            brainActive,
            playbackActive,
            captureReady
        );
    }

    static boolean shouldTimeout(
        boolean voiceActive,
        VoiceEndpoint endpoint,
        boolean brainActive,
        boolean playbackActive,
        boolean captureReady
    ) {
        return voiceActive
            && endpoint == VoiceEndpoint.PHONE
            && captureReady
            && !brainActive
            && !playbackActive;
    }

    /*
     * Compatibility overload retained for existing callers and
     * historical tests while production ownership uses the
     * capture-ready form above.
     */
    static boolean shouldTimeout(
        boolean voiceActive,
        VoiceEndpoint endpoint,
        boolean brainActive,
        boolean playbackActive
    ) {
        return shouldTimeout(
            voiceActive,
            endpoint,
            brainActive,
            playbackActive,
            true
        );
    }
}
