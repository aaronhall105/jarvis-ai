package com.aaron.jarvisvoice;

import org.junit.Test;
import static org.junit.Assert.*;

public class TranscriptPolicyTest {
    @Test public void wakeCommandAccepted() {
        var result = TranscriptPolicy.evaluate("Jarvis turn the bedroom light off", "", false, false, "jarvis");
        assertEquals(TranscriptPolicy.Action.COMMAND, result.action);
        assertEquals("turn the bedroom light off", result.command);
    }
    @Test public void idleSpeechWithoutWakeIgnored() {
        assertEquals(TranscriptPolicy.Action.IGNORE,
            TranscriptPolicy.evaluate("turn the light off", "", false, false, "jarvis").action);
    }
    @Test public void expectedFollowUpAccepted() {
        assertEquals(TranscriptPolicy.Action.COMMAND,
            TranscriptPolicy.evaluate("the bedroom", "", true, false, "jarvis").action);
    }
    @Test public void playbackRequiresWake() {
        assertEquals(TranscriptPolicy.Action.IGNORE,
            TranscriptPolicy.evaluate("turn it off", "the lights are on", false, true, "jarvis").action);
    }
    @Test public void stopInterruptsPlayback() {
        assertEquals(TranscriptPolicy.Action.STOP,
            TranscriptPolicy.evaluate("Jarvis stop", "a long response", false, true, "jarvis").action);
    }
    @Test public void replacementCommandInterruptsPlayback() {
        assertEquals(TranscriptPolicy.Action.COMMAND,
            TranscriptPolicy.evaluate("Jarvis open YouTube instead", "a long response", false, true, "jarvis").action);
    }
    @Test public void assistantEchoIgnored() {
        assertEquals(TranscriptPolicy.Action.IGNORE,
            TranscriptPolicy.evaluate("the bedroom and hallway lights are on", "The bedroom and hallway lights are on.", false, true, "jarvis").action);
    }
}
