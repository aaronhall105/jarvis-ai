package com.aaron.jarvisvoice;

/** Generation ownership for binary response audio from text and VAD-created turns. */
final class RealtimeAudioOwnership {
    private RealtimeAudioOwnership() {}

    static boolean accepts(int audioGeneration, int minimumGeneration) {
        return audioGeneration > 0 && audioGeneration >= minimumGeneration;
    }
}
