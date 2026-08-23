package com.aaron.jarvisvoice;

import android.content.Context;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;

/**
 * App-private crash journal for one ambiguous realtime text
 * turn.
 *
 * The record is committed before the corresponding command is
 * allowed onto the WebSocket. Process death can therefore lose
 * unused work, but cannot make an admitted command look new.
 */
final class TurnRecoveryJournal {
    static final String FILE_NAME =
        "jarvis_realtime_turn_recovery.json";

    /*
     * A process restart normally recovers in seconds. Never
     * authorise automatic replay of a journal that has survived
     * for an unexpectedly long period: Core's own ledger may
     * have been reset/pruned by then.
     */
    static final long MAX_REPLAY_AGE_MS =
        15L * 60L * 1_000L;

    static final int MAX_TEXT_CHARS =
        131_072;

    record Snapshot(
        long clientTurnId,
        String text,
        boolean speak,
        String conversationId,
        String endpoint,
        boolean responseDelivered,
        long createdAtMs
    ) {
        boolean matchesIdentity(
            String conversationId,
            String endpoint
        ) {
            return this.conversationId.equals(
                safe(conversationId)
            )
                && this.endpoint.equals(
                    normaliseEndpoint(endpoint)
                );
        }

        boolean replayFresh(
            long nowMs
        ) {
            if (
                createdAtMs <= 0L
                    || nowMs < createdAtMs
            ) {
                return false;
            }

            return nowMs - createdAtMs
                <= MAX_REPLAY_AGE_MS;
        }
    }

    private final Path file;

    TurnRecoveryJournal(
        Context context
    ) {
        this(
            applicationContext(context)
                .getFilesDir()
                .toPath()
                .resolve(FILE_NAME)
        );
    }

    TurnRecoveryJournal(
        Path file
    ) {
        if (file == null) {
            throw new IllegalArgumentException(
                "file must not be null"
            );
        }

        this.file = file;
    }

    synchronized void save(
        Snapshot snapshot
    ) throws IOException {
        Snapshot checked =
            validate(snapshot);

        JSONObject root =
            new JSONObject();

        try {
            root.put(
                "client_turn_id",
                checked.clientTurnId()
            );

            root.put(
                "text",
                checked.text()
            );

            root.put(
                "speak",
                checked.speak()
            );

            root.put(
                "conversation_id",
                checked.conversationId()
            );

            root.put(
                "endpoint",
                checked.endpoint()
            );

            root.put(
                "response_delivered",
                checked.responseDelivered()
            );

            root.put(
                "created_at_ms",
                checked.createdAtMs()
            );

        } catch (
            JSONException exception
        ) {
            throw new IOException(
                "Could not encode realtime recovery journal",
                exception
            );
        }

        atomicWrite(
            root.toString()
        );
    }

    synchronized Snapshot load()
        throws IOException {

        if (!Files.exists(file)) {
            return null;
        }

        String raw =
            new String(
                Files.readAllBytes(
                    file
                ),
                StandardCharsets.UTF_8
            );

        try {
            JSONObject root =
                new JSONObject(raw);

            Snapshot snapshot =
                new Snapshot(
                    root.optLong(
                        "client_turn_id",
                        0L
                    ),
                    root.optString(
                        "text",
                        ""
                    ),
                    root.optBoolean(
                        "speak",
                        false
                    ),
                    root.optString(
                        "conversation_id",
                        ""
                    ),
                    root.optString(
                        "endpoint",
                        ""
                    ),
                    root.optBoolean(
                        "response_delivered",
                        false
                    ),
                    root.optLong(
                        "created_at_ms",
                        0L
                    )
                );

            return validate(
                snapshot
            );

        } catch (Exception invalid) {
            /*
             * A malformed journal can never be treated as
             * replay authority.
             */
            Files.deleteIfExists(file);
            return null;
        }
    }

    synchronized boolean markResponseDelivered(
        long clientTurnId,
        String conversationId,
        String endpoint
    ) throws IOException {
        Snapshot existing =
            load();

        if (
            existing == null
                || existing.clientTurnId()
                    != clientTurnId
                || !existing.matchesIdentity(
                    conversationId,
                    endpoint
                )
        ) {
            return false;
        }

        save(
            new Snapshot(
                existing.clientTurnId(),
                existing.text(),
                existing.speak(),
                existing.conversationId(),
                existing.endpoint(),
                true,
                existing.createdAtMs()
            )
        );

        return true;
    }

    synchronized boolean clearMatching(
        long clientTurnId,
        String conversationId,
        String endpoint
    ) throws IOException {
        Snapshot existing =
            load();

        if (
            existing == null
                || existing.clientTurnId()
                    != clientTurnId
                || !existing.matchesIdentity(
                    conversationId,
                    endpoint
                )
        ) {
            return false;
        }

        return Files.deleteIfExists(
            file
        );
    }

    synchronized void clear()
        throws IOException {

        Files.deleteIfExists(
            file
        );
    }

    private void atomicWrite(
        String value
    ) throws IOException {
        Path parent =
            file.toAbsolutePath()
                .getParent();

        if (parent == null) {
            throw new IOException(
                "Recovery journal has no parent"
            );
        }

        Files.createDirectories(
            parent
        );

        Path temporary =
            Files.createTempFile(
                parent,
                file.getFileName()
                    .toString()
                    + ".",
                ".tmp"
            );

        byte[] bytes =
            value.getBytes(
                StandardCharsets.UTF_8
            );

        try {
            try (
                FileChannel channel =
                    FileChannel.open(
                        temporary,
                        StandardOpenOption.WRITE,
                        StandardOpenOption
                            .TRUNCATE_EXISTING
                    )
            ) {
                ByteBuffer buffer =
                    ByteBuffer.wrap(bytes);

                while (
                    buffer.hasRemaining()
                ) {
                    channel.write(
                        buffer
                    );
                }

                /*
                 * Process-death durability: file contents reach
                 * the filesystem before the atomic replacement.
                 */
                channel.force(true);
            }

            try {
                Files.move(
                    temporary,
                    file,
                    StandardCopyOption
                        .ATOMIC_MOVE,
                    StandardCopyOption
                        .REPLACE_EXISTING
                );

            } catch (
                AtomicMoveNotSupportedException
                    unsupported
            ) {
                Files.move(
                    temporary,
                    file,
                    StandardCopyOption
                        .REPLACE_EXISTING
                );
            }

        } finally {
            Files.deleteIfExists(
                temporary
            );
        }
    }

    private static Snapshot validate(
        Snapshot snapshot
    ) {
        if (snapshot == null) {
            throw new IllegalArgumentException(
                "snapshot must not be null"
            );
        }

        if (
            snapshot.clientTurnId()
                <= 0L
        ) {
            throw new IllegalArgumentException(
                "clientTurnId must be positive"
            );
        }

        String text =
            safe(snapshot.text());

        if (text.isEmpty()) {
            throw new IllegalArgumentException(
                "text must not be empty"
            );
        }

        if (
            text.length()
                > MAX_TEXT_CHARS
        ) {
            throw new IllegalArgumentException(
                "text exceeds recovery limit"
            );
        }

        String conversationId =
            safe(
                snapshot.conversationId()
            );

        if (
            conversationId.isEmpty()
        ) {
            throw new IllegalArgumentException(
                "conversationId must not be empty"
            );
        }

        String endpoint =
            normaliseEndpoint(
                snapshot.endpoint()
            );

        if (
            snapshot.createdAtMs()
                <= 0L
        ) {
            throw new IllegalArgumentException(
                "createdAtMs must be positive"
            );
        }

        return new Snapshot(
            snapshot.clientTurnId(),
            text,
            snapshot.speak(),
            conversationId,
            endpoint,
            snapshot.responseDelivered(),
            snapshot.createdAtMs()
        );
    }

    private static String safe(
        String value
    ) {
        return value == null
            ? ""
            : value.trim();
    }

    private static String normaliseEndpoint(
        String value
    ) {
        String normalised =
            safe(value)
                .toUpperCase();

        if (
            !"PHONE".equals(normalised)
                && !"WATCH".equals(normalised)
        ) {
            throw new IllegalArgumentException(
                "endpoint must be PHONE or WATCH"
            );
        }

        return normalised;
    }

    private static Context applicationContext(
        Context context
    ) {
        if (context == null) {
            throw new IllegalArgumentException(
                "context must not be null"
            );
        }

        Context application =
            context.getApplicationContext();

        return application == null
            ? context
            : application;
    }
}
