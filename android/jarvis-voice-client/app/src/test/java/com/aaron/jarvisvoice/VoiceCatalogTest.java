package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class VoiceCatalogTest {
    @Test public void jarvisUsesReliableRealtimeAudio() {
        assertFalse(VoiceCatalog.isOriginal("original"));
        assertEquals("realtime", VoiceCatalog.serverMode("original"));
        assertEquals("marin", VoiceCatalog.serverVoice("original"));
    }

    @Test public void homeAssistantOriginalRemainsSelectable() {
        assertTrue(VoiceCatalog.isOriginal(
            VoiceCatalog.HOME_ASSISTANT_ID
        ));
        assertEquals(
            "home_assistant",
            VoiceCatalog.serverMode(VoiceCatalog.HOME_ASSISTANT_ID)
        );
        assertEquals(
            "marin",
            VoiceCatalog.serverVoice(VoiceCatalog.HOME_ASSISTANT_ID)
        );
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
