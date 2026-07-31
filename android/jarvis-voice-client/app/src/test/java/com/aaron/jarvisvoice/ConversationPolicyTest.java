package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class ConversationPolicyTest {
    @Test public void titleIsShortAndUseful() {
        assertEquals(
            "Turn the living room lights off",
            ConversationPolicy.titleFromText(
                "Turn the living room lights off"
            )
        );
        assertTrue(
            ConversationPolicy.titleFromText(
                "This is a deliberately long conversation title "
                    + "that needs shortening for the chat list"
            ).endsWith("…")
        );
    }

    @Test public void ownerComparisonIsIsolated() {
        assertTrue(
            ConversationPolicy.sameOwner(
                "Aaron",
                " aaron "
            )
        );
        assertFalse(
            ConversationPolicy.sameOwner(
                "Aaron",
                "Amber"
            )
        );
    }

    @Test public void sectionsAreStable() {
        long today = 1_000_000L;
        long sevenDays = 400_000L;
        assertEquals(
            ConversationPolicy.TODAY,
            ConversationPolicy.section(
                1_100_000L,
                today,
                sevenDays
            )
        );
        assertEquals(
            ConversationPolicy.PREVIOUS_7_DAYS,
            ConversationPolicy.section(
                700_000L,
                today,
                sevenDays
            )
        );
        assertEquals(
            ConversationPolicy.OLDER,
            ConversationPolicy.section(
                200_000L,
                today,
                sevenDays
            )
        );
    }
}
