package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class TurnRecoveryStateTest {
    @Test public void transportLossPreservesExactLogicalTurn() {
        TurnRecoveryState state =
            new TurnRecoveryState();

        state.begin(
            1_900_000_000_001L,
            "Turn the lights off",
            true
        );

        state.onTransportLost();

        assertTrue(state.hasPending());

        assertEquals(
            1_900_000_000_001L,
            state.clientTurnId()
        );

        assertEquals(
            "Turn the lights off",
            state.text()
        );

        assertTrue(state.speak());
    }

    @Test public void unknownMatchingTurnAllowsReplay() {
        TurnRecoveryState state =
            new TurnRecoveryState();

        state.begin(
            55L,
            "Lock the door",
            false
        );

        assertEquals(
            TurnRecoveryPolicy.Action.REPLAY_UNKNOWN,
            state.action(
                55L,
                false,
                "unknown",
                false
            )
        );
    }

    @Test public void acceptedMatchingTurnNeverReplays() {
        TurnRecoveryState state =
            new TurnRecoveryState();

        state.begin(
            56L,
            "Lock the door",
            false
        );

        assertEquals(
            TurnRecoveryPolicy.Action.WAIT_ACCEPTED,
            state.action(
                56L,
                true,
                "accepted",
                false
            )
        );
    }

    @Test public void completedMatchingTurnRestores() {
        TurnRecoveryState state =
            new TurnRecoveryState();

        state.begin(
            57L,
            "Status",
            false
        );

        assertEquals(
            TurnRecoveryPolicy.Action.RESTORE_COMPLETED,
            state.action(
                57L,
                true,
                "completed",
                false
            )
        );
    }

    @Test public void mismatchedStatusCannotControlPendingTurn() {
        TurnRecoveryState state =
            new TurnRecoveryState();

        state.begin(
            58L,
            "Kitchen lights off",
            true
        );

        assertEquals(
            TurnRecoveryPolicy.Action.IGNORE,
            state.action(
                59L,
                false,
                "unknown",
                false
            )
        );
    }

    @Test public void deliveredResponseSurvivesHandover() {
        TurnRecoveryState state =
            new TurnRecoveryState();

        state.begin(
            60L,
            "What time is it",
            true
        );

        state.markResponseDelivered(
            60L
        );

        state.onTransportLost();

        assertTrue(
            state.responseDelivered()
        );
    }

    @Test public void newLogicalTurnSupersedesOldIntent() {
        TurnRecoveryState state =
            new TurnRecoveryState();

        state.begin(
            61L,
            "First",
            true
        );

        state.begin(
            62L,
            "Second",
            false
        );

        assertFalse(state.matches(61L));
        assertTrue(state.matches(62L));

        assertEquals(
            "Second",
            state.text()
        );

        assertFalse(state.speak());
    }

    @Test public void clearRemovesReplayAuthority() {
        TurnRecoveryState state =
            new TurnRecoveryState();

        state.begin(
            63L,
            "Do something",
            true
        );

        state.clear();

        assertFalse(state.hasPending());

        assertEquals(
            TurnRecoveryPolicy.Action.IGNORE,
            state.action(
                63L,
                false,
                "unknown",
                false
            )
        );
    }

    @Test public void statusCounterResetsAfterTransportLoss() {
        TurnRecoveryState state =
            new TurnRecoveryState();

        state.begin(
            64L,
            "Test",
            false
        );

        assertEquals(
            1,
            state.noteStatusCheck()
        );

        assertEquals(
            2,
            state.noteStatusCheck()
        );

        state.onTransportLost();

        assertEquals(
            1,
            state.noteStatusCheck()
        );
    }

    @Test public void restorePreservesDurableResponseDelivery() {
        TurnRecoveryState state =
            new TurnRecoveryState();

        state.restore(
            90L,
            "Recovered request",
            true,
            true
        );

        assertTrue(
            state.hasPending()
        );

        assertEquals(
            90L,
            state.clientTurnId()
        );

        assertEquals(
            "Recovered request",
            state.text()
        );

        assertTrue(
            state.speak()
        );

        assertTrue(
            state.responseDelivered()
        );
    }

    @Test public void restoreCanPreserveUndeliveredResponse() {
        TurnRecoveryState state =
            new TurnRecoveryState();

        state.restore(
            91L,
            "Recovered request",
            false,
            false
        );

        assertTrue(
            state.hasPending()
        );

        assertFalse(
            state.speak()
        );

        assertFalse(
            state.responseDelivered()
        );
    }

}
