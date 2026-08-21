package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public final class BargeInAudioPolicyTest {
    @Test public void openSpeakerIsAttenuatedDuringFullDuplexCapture() {
        assertEquals(0.55f, BargeInAudioPolicy.outputGain(false, true), 0.001f);
    }

    @Test public void privateRoutesAndNonBargeInPlaybackKeepFullGain() {
        assertEquals(1.0f, BargeInAudioPolicy.outputGain(true, true), 0.001f);
        assertEquals(1.0f, BargeInAudioPolicy.outputGain(false, false), 0.001f);
    }
}
