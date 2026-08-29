package com.aaron.jarvisvoice.protocol;

import static org.junit.Assert.*;
import org.junit.Test;

public class SpeechSilencePolicyTest {
    @Test public void timeoutIsEightSeconds() {
        assertEquals(8_000L, SpeechSilencePolicy.DEFAULT_TIMEOUT_MS);
    }

    @Test public void silenceDoesNotCountAsSpeech() {
        SpeechSilencePolicy policy = new SpeechSilencePolicy();
        for (int index = 0; index < 20; index++) assertFalse(policy.acceptPcm16(pcm(120)));
        assertFalse(policy.speechStarted());
    }

    @Test public void genuineSpeechCancelsSilenceTimeoutAndResetRearmsIt() {
        SpeechSilencePolicy policy = new SpeechSilencePolicy();
        assertFalse(policy.acceptPcm16(pcm(4_000)));
        assertFalse(policy.acceptPcm16(pcm(4_000)));
        assertTrue(policy.acceptPcm16(pcm(4_000)));
        policy.reset();
        assertFalse(policy.speechStarted());
        assertFalse(policy.acceptPcm16(pcm(4_000)));
    }

    private static byte[] pcm(int amplitude) {
        byte[] bytes = new byte[960];
        for (int index = 0; index < bytes.length; index += 2) {
            bytes[index] = (byte) amplitude;
            bytes[index + 1] = (byte) (amplitude >>> 8);
        }
        return bytes;
    }
}
