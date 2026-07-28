package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class WakePhrasePolicyTest {
    @Test public void exactJarvisWakesWithoutCommand() {
        WakePhrasePolicy.Decision result = WakePhrasePolicy.evaluate("Jarvis", "jarvis");
        assertTrue(result.triggered);
        assertEquals("", result.command);
    }

    @Test public void commandAfterWakePhraseIsPreserved() {
        WakePhrasePolicy.Decision result = WakePhrasePolicy.evaluate(
            "Hey Jarvis, where is Amber?",
            "jarvis"
        );
        assertTrue(result.triggered);
        assertEquals("where is amber", result.command);
    }

    @Test public void normalConversationDoesNotWake() {
        assertFalse(WakePhrasePolicy.evaluate("Amber is in the kitchen", "jarvis").triggered);
    }

    @Test public void commonJervisVariantWakes() {
        assertTrue(WakePhrasePolicy.evaluate("Jervis turn the light off", "jarvis").triggered);
    }
}
