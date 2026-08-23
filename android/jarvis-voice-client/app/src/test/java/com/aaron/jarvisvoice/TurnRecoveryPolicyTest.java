package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class TurnRecoveryPolicyTest {
    @Test public void unknownIsTheOnlyAutomaticReplayState() {
        assertEquals(
            TurnRecoveryPolicy.Action.REPLAY_UNKNOWN,
            TurnRecoveryPolicy.action(
                false,
                "unknown",
                false
            )
        );

        assertTrue(
            TurnRecoveryPolicy.mayReplay(
                false,
                "unknown",
                false
            )
        );

        assertFalse(
            TurnRecoveryPolicy.mayReplay(
                true,
                "accepted",
                false
            )
        );

        assertFalse(
            TurnRecoveryPolicy.mayReplay(
                true,
                "interrupted",
                false
            )
        );
    }

    @Test public void acceptedNeverReplaysAutomatically() {
        assertEquals(
            TurnRecoveryPolicy.Action.WAIT_ACCEPTED,
            TurnRecoveryPolicy.action(
                true,
                "accepted",
                false
            )
        );
    }

    @Test public void completedRestoresInsteadOfReplaying() {
        TurnRecoveryPolicy.Action action =
            TurnRecoveryPolicy.action(
                true,
                "completed",
                false
            );

        assertEquals(
            TurnRecoveryPolicy.Action.RESTORE_COMPLETED,
            action
        );

        assertTrue(
            TurnRecoveryPolicy.isTerminal(
                action
            )
        );
    }

    @Test public void cancelledAndInterruptedAreTerminal() {
        assertEquals(
            TurnRecoveryPolicy.Action.FINISH_CANCELLED,
            TurnRecoveryPolicy.action(
                true,
                "cancelled",
                false
            )
        );

        assertEquals(
            TurnRecoveryPolicy.Action.FINISH_INTERRUPTED,
            TurnRecoveryPolicy.action(
                true,
                "interrupted",
                false
            )
        );
    }

    @Test public void conflictNeverReplays() {
        TurnRecoveryPolicy.Action action =
            TurnRecoveryPolicy.action(
                true,
                "completed",
                true
            );

        assertEquals(
            TurnRecoveryPolicy.Action.FAIL_CONFLICT,
            action
        );

        assertFalse(
            TurnRecoveryPolicy.mayReplay(
                true,
                "completed",
                true
            )
        );
    }
}
