package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class WakeReliabilityAlpha11Test {
    @Test public void strictModeRejectsBareJarvis() {
        assertFalse(
            WakePhrasePolicy.evaluate(
                "Jarvis",
                "hey jarvis"
            ).triggered
        );
    }

    @Test public void strictModeAcceptsHeyJarvisCommand() {
        WakePhrasePolicy.Decision result =
            WakePhrasePolicy.evaluate(
                "Hey Jarvis turn the bedroom light off",
                "hey jarvis"
            );

        assertTrue(result.triggered);
        assertEquals(
            "turn the bedroom light off",
            result.command
        );
    }

    @Test public void fillerAfterWakeIsRejected() {
        assertEquals(
            "",
            WakeCommandPolicy.commandAfterWake(
                "okay",
                "hey jarvis"
            )
        );
    }

    @Test public void meaningfulFollowUpIsAccepted() {
        assertEquals(
            "turn the bedroom light off",
            WakeCommandPolicy.commandAfterWake(
                "turn the bedroom light off",
                "hey jarvis"
            )
        );
    }

    @Test public void repeatedWakePhraseIsStripped() {
        assertEquals(
            "what time is it",
            WakeCommandPolicy.commandAfterWake(
                "Hey Jarvis what time is it",
                "hey jarvis"
            )
        );
    }
}
