package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public final class ConversationModeTest {
    @Test public void liveIsDefault() {
        assertEquals("live", ConversationMode.normalise(null));
        assertEquals("live", ConversationMode.normalise("unknown"));
    }

    @Test public void modeCanToggleBothWays() {
        assertEquals("standard", ConversationMode.toggle("live"));
        assertEquals("live", ConversationMode.toggle("standard"));
    }
}
