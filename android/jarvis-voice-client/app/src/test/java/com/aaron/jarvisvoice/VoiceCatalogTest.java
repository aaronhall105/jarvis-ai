package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class VoiceCatalogTest {
    @Test public void originalJarvisUsesHomeAssistantAudio() {
        assertTrue(
            VoiceCatalog.isOriginal(
                VoiceCatalog.ORIGINAL_ID
            )
        );
        assertEquals(
            VoiceCatalog.MODE_HOME_ASSISTANT,
            VoiceCatalog.serverMode(
                VoiceCatalog.ORIGINAL_ID
            )
        );
        assertEquals(
            "marin",
            VoiceCatalog.serverVoice(
                VoiceCatalog.ORIGINAL_ID
            )
        );
    }

    @Test public void homeAssistantAliasUsesOriginalJarvis() {
        assertTrue(
            VoiceCatalog.isOriginal(
                VoiceCatalog.HOME_ASSISTANT_ID
            )
        );
        assertEquals(
            VoiceCatalog.ORIGINAL_ID,
            VoiceCatalog.fromId(
                VoiceCatalog.HOME_ASSISTANT_ID
            ).id
        );
        assertEquals(
            VoiceCatalog.MODE_HOME_ASSISTANT,
            VoiceCatalog.serverMode(
                VoiceCatalog.HOME_ASSISTANT_ID
            )
        );
    }

    @Test public void realtimeVoicesRemainSelectable() {
        assertFalse(
            VoiceCatalog.isOriginal("cedar")
        );
        assertEquals(
            VoiceCatalog.MODE_REALTIME,
            VoiceCatalog.serverMode("cedar")
        );
        assertEquals(
            "cedar",
            VoiceCatalog.serverVoice("cedar")
        );
    }

    @Test public void unknownVoiceFallsBackToOriginalJarvis() {
        assertEquals(
            VoiceCatalog.ORIGINAL_ID,
            VoiceCatalog.fromId("unknown").id
        );
        assertEquals(
            VoiceCatalog.MODE_HOME_ASSISTANT,
            VoiceCatalog.serverMode("unknown")
        );
    }
}
