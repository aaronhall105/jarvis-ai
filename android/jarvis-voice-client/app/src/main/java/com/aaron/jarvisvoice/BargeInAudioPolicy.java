package com.aaron.jarvisvoice;

/** Acoustic policy for full-duplex recognition while assistant speech plays. */
public final class BargeInAudioPolicy {
    public static final float OPEN_SPEAKER_OUTPUT_GAIN = 0.55f;

    private BargeInAudioPolicy() {}

    public static float outputGain(boolean privateRoute, boolean bargeInArmed) {
        if (!bargeInArmed || privateRoute) return 1.0f;
        return OPEN_SPEAKER_OUTPUT_GAIN;
    }
}
