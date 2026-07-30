package com.aaron.jarvisvoice;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class SpeechFallbackPolicyTest {
    @Test public void missingRealtimeAudioUsesFallback() {
        assertTrue(SpeechFallbackPolicy.shouldUseFallback(
            true,
            false,
            true,
            "Ready, Aaron.",
            false
        ));
    }

    @Test public void realtimeAudioPreventsDuplicateSpeech() {
        assertFalse(SpeechFallbackPolicy.shouldUseFallback(
            true,
            true,
            true,
            "Ready, Aaron.",
            false
        ));
    }

    @Test public void silentTextTurnStaysSilent() {
        assertFalse(SpeechFallbackPolicy.shouldUseFallback(
            false,
            false,
            true,
            "Text response",
            false
        ));
    }

    @Test public void homeAssistantVoiceKeepsItsOwnRenderer() {
        assertFalse(SpeechFallbackPolicy.shouldUseFallback(
            true,
            false,
            true,
            "Home Assistant response",
            true
        ));
    }
}
