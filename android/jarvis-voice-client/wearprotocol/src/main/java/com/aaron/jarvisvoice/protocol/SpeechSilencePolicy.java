package com.aaron.jarvisvoice.protocol;

/** Detects genuine near-field speech without treating normal microphone noise as activity. */
public final class SpeechSilencePolicy {
    public static final long DEFAULT_TIMEOUT_MS = 8_000L;
    private static final int SPEECH_PEAK = 1_100;
    private static final int REQUIRED_FRAMES = 3;
    private int consecutiveSpeechFrames;
    private boolean speechStarted;

    public synchronized boolean acceptPcm16(byte[] pcm) {
        if (speechStarted || pcm == null) return speechStarted;
        int peak = 0;
        for (int index = 0; index + 1 < pcm.length; index += 2) {
            int sample = (short) ((pcm[index] & 0xff) | (pcm[index + 1] << 8));
            peak = Math.max(peak, Math.abs(sample));
        }
        consecutiveSpeechFrames = peak >= SPEECH_PEAK ? consecutiveSpeechFrames + 1 : 0;
        speechStarted = consecutiveSpeechFrames >= REQUIRED_FRAMES;
        return speechStarted;
    }

    public synchronized void reset() {
        consecutiveSpeechFrames = 0;
        speechStarted = false;
    }

    public synchronized boolean speechStarted() { return speechStarted; }
}
