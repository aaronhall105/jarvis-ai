package com.aaron.jarvisvoice;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class PlaybackEchoPolicyTest {
    @Test public void blocksAssistantPrefixDuringPlayback() {
        assertTrue(PlaybackEchoPolicy.isLikelyEcho(
            "do you want me to stop listening for voice",
            "Do you want me to stop listening for voice commands now "
                + "(disable voice mode)?",
            true
        ));
    }

    @Test public void blocksAssistantSpeechAfterPlaybackCallback() {
        assertTrue(PlaybackEchoPolicy.isLikelyEcho(
            "if you want me to stop listening say stop listening now",
            "If you want me to stop listening, say stop listening now "
                + "and I will disable voice mode.",
            true
        ));
    }

    @Test public void allowsDifferentBargeInCommand() {
        assertFalse(PlaybackEchoPolicy.isLikelyEcho(
            "turn the kitchen lights off",
            "The weather tomorrow will be cloudy with light rain.",
            true
        ));
    }

    @Test public void allowsJarvisStopEvenWhenReplyMentionsStop() {
        assertFalse(PlaybackEchoPolicy.isLikelyEcho(
            "jarvis stop",
            "Say stop listening if you want me to stop.",
            true
        ));
    }

    @Test public void neverFiltersOutsidePlaybackWindow() {
        assertFalse(PlaybackEchoPolicy.isLikelyEcho(
            "do you want me to stop listening for voice",
            "Do you want me to stop listening for voice commands now?",
            false
        ));
    }

    @Test public void blocksObservedCurlyApostropheEcho() {
        assertTrue(PlaybackEchoPolicy.isLikelyEcho(
            "concerned about someone's hygiene or a smell in the house",
            "If you’re concerned about someone’s hygiene or a smell in "
                + "the house, tell me what you’ve noticed.",
            true
        ));
    }

    @Test public void blocksObservedContractionEcho() {
        assertTrue(PlaybackEchoPolicy.isLikelyEcho(
            "chocolate starfish i can't help",
            "I can’t help with sexual requests or explicit content.",
            true
        ));
    }
}
