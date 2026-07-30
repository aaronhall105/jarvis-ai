package com.aaron.jarvisvoice;

public final class SpeechFallbackPolicy {
    private SpeechFallbackPolicy() {}

    public static boolean shouldUseFallback(
        boolean shouldSpeak,
        boolean realtimeAudioReceived,
        boolean successful,
        String responseText,
        boolean homeAssistantVoice
    ) {
        return shouldSpeak
            && !realtimeAudioReceived
            && successful
            && responseText != null
            && !responseText.isBlank()
            && !homeAssistantVoice;
    }
}
