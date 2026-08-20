package com.aaron.jarvisvoice;

final class OriginalVoiceFallbackPolicy {
    private OriginalVoiceFallbackPolicy() {}

    static boolean shouldFallback(
        boolean voiceActive,
        String pendingSpeech,
        boolean mediaPlaybackStarted,
        boolean fallbackPending,
        boolean fallbackSpeaking,
        boolean fallbackAvailable
    ) {
        return voiceActive
            && pendingSpeech != null
            && !pendingSpeech.isBlank()
            && !mediaPlaybackStarted
            && !fallbackPending
            && !fallbackSpeaking
            && fallbackAvailable;
    }
}
