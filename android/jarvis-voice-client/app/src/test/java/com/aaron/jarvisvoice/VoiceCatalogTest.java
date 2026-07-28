package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class VoiceCatalogTest {
    @Test public void originalUsesHomeAssistantMode() {
        assertTrue(VoiceCatalog.isOriginal("original"));
        assertEquals("home_assistant", VoiceCatalog.serverMode("original"));
        assertEquals("marin", VoiceCatalog.serverVoice("original"));
    }

    @Test public void realtimeVoicesRemainSelectable() {
        assertFalse(VoiceCatalog.isOriginal("cedar"));
        assertEquals("realtime", VoiceCatalog.serverMode("cedar"));
        assertEquals("cedar", VoiceCatalog.serverVoice("cedar"));
    }

    @Test public void unknownVoiceFallsBackToMarin() {
        assertEquals("marin", VoiceCatalog.fromId("unknown").id);
    }
}
