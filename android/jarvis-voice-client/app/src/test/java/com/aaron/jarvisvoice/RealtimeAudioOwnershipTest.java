package com.aaron.jarvisvoice;

import static org.junit.Assert.*;
import org.junit.Test;

public class RealtimeAudioOwnershipTest {
    @Test public void coldFirstVadTurnDoesNotRequireTextTurnId() {
        assertTrue(RealtimeAudioOwnership.accepts(1, 1));
    }

    @Test public void rejectsAudioBeforeBrainOwnershipAndAfterCancellation() {
        assertFalse(RealtimeAudioOwnership.accepts(0, 0));
        assertFalse(RealtimeAudioOwnership.accepts(4, 5));
        assertTrue(RealtimeAudioOwnership.accepts(5, 5));
    }
}
