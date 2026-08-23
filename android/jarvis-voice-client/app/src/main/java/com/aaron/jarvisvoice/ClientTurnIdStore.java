package com.aaron.jarvisvoice;

import android.content.Context;
import android.content.SharedPreferences;

/**
 * Allocates positive client_turn_id values that remain unique
 * across JarvisRealtimeClient recreation and process restart.
 *
 * A block is synchronously reserved before any id from that
 * block is exposed. If Android dies afterwards, unused ids are
 * simply skipped; they are never reused.
 */
final class ClientTurnIdStore {
    static final long BLOCK_SIZE = 4_096L;

    private static final long MINIMUM_INITIAL_ID =
        1_000_000_000_000L;

    private static final String PREFS =
        "jarvis_realtime_turn_ids";

    private static final String KEY_NEXT_UNRESERVED =
        "next_unreserved_client_turn_id";

    private static final Object RESERVATION_LOCK =
        new Object();

    private final SharedPreferences values;

    private long next;
    private long limitExclusive;

    ClientTurnIdStore(
        Context context
    ) {
        Context application =
            context.getApplicationContext();

        Context owner = application == null
            ? context
            : application;

        values = owner.getSharedPreferences(
            PREFS,
            Context.MODE_PRIVATE
        );

        reserveBlock();
    }

    synchronized long next() {
        if (next >= limitExclusive) {
            reserveBlock();
        }

        long value = next++;

        if (value <= 0L) {
            throw new IllegalStateException(
                "client_turn_id must remain positive"
            );
        }

        return value;
    }

    private void reserveBlock() {
        synchronized (RESERVATION_LOCK) {
            long stored = values.getLong(
                KEY_NEXT_UNRESERVED,
                0L
            );

            long base = stored > 0L
                ? stored
                : initialBase(
                    System.currentTimeMillis()
                );

            long end = blockEnd(base);

            boolean persisted = values.edit()
                .putLong(
                    KEY_NEXT_UNRESERVED,
                    end
                )
                .commit();

            if (!persisted) {
                throw new IllegalStateException(
                    "Could not reserve client turn ids"
                );
            }

            next = base;
            limitExclusive = end;
        }
    }

    static long initialBase(
        long wallClockMillis
    ) {
        return Math.max(
            MINIMUM_INITIAL_ID,
            wallClockMillis
        );
    }

    static long blockEnd(
        long base
    ) {
        if (base <= 0L) {
            throw new IllegalArgumentException(
                "base must be positive"
            );
        }

        if (
            base
                > Long.MAX_VALUE
                    - BLOCK_SIZE
        ) {
            throw new IllegalStateException(
                "client turn id space exhausted"
            );
        }

        return base + BLOCK_SIZE;
    }
}
