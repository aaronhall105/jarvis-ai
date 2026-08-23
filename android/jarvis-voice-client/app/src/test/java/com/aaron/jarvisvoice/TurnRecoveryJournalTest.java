package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import org.junit.Test;

public final class TurnRecoveryJournalTest {
    private static TurnRecoveryJournal.Snapshot snapshot(
        long id,
        String text,
        boolean speak,
        boolean delivered,
        long created
    ) {
        return new TurnRecoveryJournal.Snapshot(
            id,
            text,
            speak,
            "chat-1",
            "PHONE",
            delivered,
            created
        );
    }

    private static TurnRecoveryJournal journal()
        throws Exception {

        Path directory =
            Files.createTempDirectory(
                "jarvis-turn-journal"
            );

        return new TurnRecoveryJournal(
            directory.resolve(
                TurnRecoveryJournal.FILE_NAME
            )
        );
    }

    @Test public void roundTripPreservesExactLogicalTurn()
        throws Exception {

        TurnRecoveryJournal journal =
            journal();

        long created =
            1_900_000_000_000L;

        journal.save(
            snapshot(
                101L,
                "Turn the lights off",
                true,
                false,
                created
            )
        );

        TurnRecoveryJournal.Snapshot loaded =
            journal.load();

        assertEquals(
            101L,
            loaded.clientTurnId()
        );

        assertEquals(
            "Turn the lights off",
            loaded.text()
        );

        assertTrue(
            loaded.speak()
        );

        assertEquals(
            "chat-1",
            loaded.conversationId()
        );

        assertEquals(
            "PHONE",
            loaded.endpoint()
        );

        assertFalse(
            loaded.responseDelivered()
        );

        assertEquals(
            created,
            loaded.createdAtMs()
        );
    }

    @Test public void laterSaveAtomicallySupersedesOldTurn()
        throws Exception {

        TurnRecoveryJournal journal =
            journal();

        journal.save(
            snapshot(
                201L,
                "First",
                true,
                false,
                1_900_000_000_000L
            )
        );

        journal.save(
            snapshot(
                202L,
                "Second",
                false,
                false,
                1_900_000_001_000L
            )
        );

        TurnRecoveryJournal.Snapshot loaded =
            journal.load();

        assertEquals(
            202L,
            loaded.clientTurnId()
        );

        assertEquals(
            "Second",
            loaded.text()
        );

        assertFalse(
            loaded.speak()
        );
    }

    @Test public void deliveredFlagPersists()
        throws Exception {

        TurnRecoveryJournal journal =
            journal();

        journal.save(
            snapshot(
                301L,
                "Status",
                false,
                false,
                1_900_000_000_000L
            )
        );

        assertTrue(
            journal.markResponseDelivered(
                301L,
                "chat-1",
                "PHONE"
            )
        );

        assertTrue(
            journal.load()
                .responseDelivered()
        );
    }

    @Test public void wrongIdentityCannotMutateJournal()
        throws Exception {

        TurnRecoveryJournal journal =
            journal();

        journal.save(
            snapshot(
                401L,
                "Lock door",
                false,
                false,
                1_900_000_000_000L
            )
        );

        assertFalse(
            journal.markResponseDelivered(
                401L,
                "another-chat",
                "PHONE"
            )
        );

        assertFalse(
            journal.clearMatching(
                401L,
                "chat-1",
                "WATCH"
            )
        );

        assertTrue(
            journal.load()
                != null
        );
    }

    @Test public void matchingTerminalTurnClearsJournal()
        throws Exception {

        TurnRecoveryJournal journal =
            journal();

        journal.save(
            snapshot(
                501L,
                "Kitchen off",
                true,
                false,
                1_900_000_000_000L
            )
        );

        assertTrue(
            journal.clearMatching(
                501L,
                "chat-1",
                "PHONE"
            )
        );

        assertNull(
            journal.load()
        );
    }

    @Test public void recentJournalMayAuthoriseUnknownReplay()
        throws Exception {

        long created =
            1_900_000_000_000L;

        TurnRecoveryJournal.Snapshot snapshot =
            snapshot(
                601L,
                "Recent command",
                false,
                false,
                created
            );

        assertTrue(
            snapshot.replayFresh(
                created
                    + TurnRecoveryJournal
                        .MAX_REPLAY_AGE_MS
            )
        );
    }

    @Test public void staleJournalCanNeverAuthoriseReplay()
        throws Exception {

        long created =
            1_900_000_000_000L;

        TurnRecoveryJournal.Snapshot snapshot =
            snapshot(
                602L,
                "Old command",
                false,
                false,
                created
            );

        assertFalse(
            snapshot.replayFresh(
                created
                    + TurnRecoveryJournal
                        .MAX_REPLAY_AGE_MS
                    + 1L
            )
        );
    }

    @Test public void futureTimestampFailsClosed()
        throws Exception {

        long created =
            1_900_000_010_000L;

        TurnRecoveryJournal.Snapshot snapshot =
            snapshot(
                603L,
                "Clock changed",
                false,
                false,
                created
            );

        assertFalse(
            snapshot.replayFresh(
                created - 1L
            )
        );
    }

    @Test public void corruptJournalIsNotReplayAuthority()
        throws Exception {

        Path directory =
            Files.createTempDirectory(
                "jarvis-corrupt-journal"
            );

        Path file =
            directory.resolve(
                TurnRecoveryJournal.FILE_NAME
            );

        Files.write(
            file,
            "{not-json".getBytes(
                StandardCharsets.UTF_8
            )
        );

        TurnRecoveryJournal journal =
            new TurnRecoveryJournal(
                file
            );

        assertNull(
            journal.load()
        );

        assertFalse(
            Files.exists(file)
        );
    }
}
