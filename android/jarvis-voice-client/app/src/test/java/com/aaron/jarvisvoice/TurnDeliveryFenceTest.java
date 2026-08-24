package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public final class TurnDeliveryFenceTest {
    @Test public void trackedUndeliveredResponseMustReconcile() {
        assertEquals(
            TurnDeliveryFence.Action.RECONCILE_COMPLETED,
            TurnDeliveryFence.afterTerminal(
                true,
                false
            )
        );
    }

    @Test public void trackedDurableResponseMayClear() {
        assertEquals(
            TurnDeliveryFence.Action.CLEAR_TERMINAL,
            TurnDeliveryFence.afterTerminal(
                true,
                true
            )
        );
    }

    @Test public void untrackedTurnDoesNotNeedDeliveryFence() {
        assertEquals(
            TurnDeliveryFence.Action.CLEAR_TERMINAL,
            TurnDeliveryFence.afterTerminal(
                false,
                false
            )
        );
    }
}
