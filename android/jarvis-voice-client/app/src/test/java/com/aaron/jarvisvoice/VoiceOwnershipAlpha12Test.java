package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class VoiceOwnershipAlpha12Test {
    @Test public void originalJarvisUsesRealtimeElevenLabsMode() {
        assertTrue(
            VoiceCatalog.isOriginal(
                VoiceCatalog.ORIGINAL_ID
            )
        );
        assertEquals(
            VoiceCatalog.MODE_REALTIME,
            VoiceCatalog.serverMode(
                VoiceCatalog.ORIGINAL_ID
            )
        );
        assertEquals(
            VoiceCatalog.ORIGINAL_ID,
            VoiceCatalog.fromId(
                VoiceCatalog.HOME_ASSISTANT_ID
            ).id
        );
    }

    @Test public void lowConfidenceBackgroundSpeechIsRejected() {
        assertFalse(
            FollowUpVoicePolicy.acceptFollowUp(
                "someone talking",
                0.20f,
                true,
                false
            )
        );
    }

    @Test public void expiredFollowUpWindowIsRejected() {
        assertFalse(
            FollowUpVoicePolicy.acceptFollowUp(
                "turn the light off",
                0.90f,
                false,
                false
            )
        );
    }

    @Test public void explicitJarvisCommandIsAccepted() {
        assertTrue(
            FollowUpVoicePolicy.acceptFollowUp(
                "Jarvis turn the light off",
                0.10f,
                false,
                false
            )
        );
        assertEquals(
            "turn the light off",
            FollowUpVoicePolicy.stripWakePrefix(
                "Jarvis turn the light off"
            )
        );
    }

    @Test public void unknownConfidenceNeedsThreeWords() {
        assertFalse(
            FollowUpVoicePolicy.acceptFollowUp(
                "light off",
                -1.0f,
                true,
                false
            )
        );
        assertTrue(
            FollowUpVoicePolicy.acceptFollowUp(
                "turn light off",
                -1.0f,
                true,
                false
            )
        );
    }

    @Test public void interruptionCommandsRemainImmediate() {
        assertTrue(
            FollowUpVoicePolicy.isImmediateInterrupt(
                "Jarvis stop"
            )
        );
    }
}
