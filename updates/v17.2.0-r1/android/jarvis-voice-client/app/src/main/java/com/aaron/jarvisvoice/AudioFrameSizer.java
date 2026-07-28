package com.aaron.jarvisvoice;

public final class AudioFrameSizer {
    private AudioFrameSizer() {}

    public static int bytesFor(int sampleRate, int milliseconds) {
        if (sampleRate <= 0 || milliseconds <= 0) throw new IllegalArgumentException("Positive values required");
        return (sampleRate * milliseconds / 1000) * 2;
    }
}
