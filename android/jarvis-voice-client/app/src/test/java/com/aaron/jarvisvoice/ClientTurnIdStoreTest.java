package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.assertThrows;

import org.junit.Test;

public final class ClientTurnIdStoreTest {
    @Test public void initialIdUsesWallClockWhenSafe() {
        long now = 1_900_000_000_000L;

        assertEquals(
            now,
            ClientTurnIdStore.initialBase(
                now
            )
        );
    }

    @Test public void initialIdNeverFallsBackToSmallLegacyIds() {
        long value =
            ClientTurnIdStore.initialBase(
                123L
            );

        assertTrue(
            value
                >= 1_000_000_000_000L
        );
    }

    @Test public void blockReservationAdvancesExactlyOneBlock() {
        long base =
            1_900_000_000_000L;

        assertEquals(
            base
                + ClientTurnIdStore
                    .BLOCK_SIZE,
            ClientTurnIdStore.blockEnd(
                base
            )
        );
    }

    @Test public void nonPositiveBaseIsRejected() {
        assertThrows(
            IllegalArgumentException.class,
            () ->
                ClientTurnIdStore.blockEnd(
                    0L
                )
        );
    }

    @Test public void overflowNeverWrapsAndReusesIds() {
        assertThrows(
            IllegalStateException.class,
            () ->
                ClientTurnIdStore.blockEnd(
                    Long.MAX_VALUE
                        - ClientTurnIdStore
                            .BLOCK_SIZE
                        + 1L
                )
        );
    }
}
