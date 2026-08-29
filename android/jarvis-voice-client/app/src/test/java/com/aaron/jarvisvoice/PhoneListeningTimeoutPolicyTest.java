package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import com.aaron.jarvisvoice.protocol.VoiceEndpoint;

import org.junit.Test;

public class PhoneListeningTimeoutPolicyTest {

    @Test
    public void deadlineIsExactlyEightSeconds() {
        assertEquals(
            8_000L,
            PhoneListeningTimeoutPolicy.TIMEOUT_MS
        );
    }

    @Test
    public void timerCannotStartBeforeCaptureIsReady() {
        assertFalse(
            PhoneListeningTimeoutPolicy.shouldArm(
                true,
                VoiceEndpoint.PHONE,
                false,
                false,
                false
            )
        );

        assertTrue(
            PhoneListeningTimeoutPolicy.shouldArm(
                true,
                VoiceEndpoint.PHONE,
                false,
                false,
                true
            )
        );
    }

    @Test
    public void idlePhoneCaptureMayTimeout() {
        assertTrue(
            PhoneListeningTimeoutPolicy.shouldTimeout(
                true,
                VoiceEndpoint.PHONE,
                false,
                false,
                true
            )
        );
    }

    @Test
    public void activeTurnPlaybackAndWatchCannotTimeout() {
        assertFalse(
            PhoneListeningTimeoutPolicy.shouldTimeout(
                true,
                VoiceEndpoint.PHONE,
                true,
                false,
                true
            )
        );

        assertFalse(
            PhoneListeningTimeoutPolicy.shouldTimeout(
                true,
                VoiceEndpoint.PHONE,
                false,
                true,
                true
            )
        );

        assertFalse(
            PhoneListeningTimeoutPolicy.shouldTimeout(
                true,
                VoiceEndpoint.WATCH,
                false,
                false,
                true
            )
        );
    }

    @Test
    public void lostCaptureCannotFireOldDeadline() {
        assertFalse(
            PhoneListeningTimeoutPolicy.shouldTimeout(
                true,
                VoiceEndpoint.PHONE,
                false,
                false,
                false
            )
        );
    }

    @Test
    public void recognizerNoiseDoesNotCountAsMeaningfulSpeech() {
        assertFalse(
            PhoneListeningTimeoutPolicy
                .isMeaningfulTranscript(null)
        );

        assertFalse(
            PhoneListeningTimeoutPolicy
                .isMeaningfulTranscript("  \n")
        );

        assertTrue(
            PhoneListeningTimeoutPolicy
                .isMeaningfulTranscript(
                    "What time is it?"
                )
        );
    }
}
