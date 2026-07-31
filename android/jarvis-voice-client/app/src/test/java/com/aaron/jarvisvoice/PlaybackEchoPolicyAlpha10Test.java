package com.aaron.jarvisvoice;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import org.junit.Test;

public final class PlaybackEchoPolicyAlpha10Test {
    @Test public void rejectsAssistantFragmentsDuringPlayback() {
        assertTrue(PlaybackEchoPolicy.isLikelyEcho("floodlight is now on", "The floodlight is now on.", true));
        assertTrue(PlaybackEchoPolicy.isLikelyEcho("done sir", "Done, sir.", true));
    }

    @Test public void preservesDifferentUserCommand() {
        assertFalse(PlaybackEchoPolicy.isLikelyEcho("turn it off", "The floodlight is now on.", true));
    }
}
