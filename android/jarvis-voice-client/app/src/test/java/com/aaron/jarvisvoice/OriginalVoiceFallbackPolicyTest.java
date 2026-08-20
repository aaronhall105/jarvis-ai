package com.aaron.jarvisvoice;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class OriginalVoiceFallbackPolicyTest {
    @Test public void explicitHomeAssistantFailureUsesDeviceSpeech() {
        assertTrue(decide(true, "It is 20:46.", false, false, false, true));
    }

    @Test public void inactiveVoiceSessionRejectsStaleFailure() {
        assertFalse(decide(false, "Old response", false, false, false, true));
    }

    @Test public void startedMediaIsNotDuplicatedByFallback() {
        assertFalse(decide(true, "Partially played", true, false, false, true));
    }

    @Test public void pendingFallbackRejectsDuplicateError() {
        assertFalse(decide(true, "Answer", false, true, false, true));
    }

    @Test public void activeFallbackRejectsDuplicateError() {
        assertFalse(decide(true, "Answer", false, false, true, true));
    }

    @Test public void missingTextCannotReachSpeechEngine() {
        assertFalse(decide(true, "", false, false, false, true));
    }

    @Test public void unavailableSpeechEngineFailsClosed() {
        assertFalse(decide(true, "Answer", false, false, false, false));
    }

    private static boolean decide(
        boolean voiceActive,
        String text,
        boolean playbackStarted,
        boolean fallbackPending,
        boolean fallbackSpeaking,
        boolean fallbackAvailable
    ) {
        return OriginalVoiceFallbackPolicy.shouldFallback(
            voiceActive,
            text,
            playbackStarted,
            fallbackPending,
            fallbackSpeaking,
            fallbackAvailable
        );
    }
}
