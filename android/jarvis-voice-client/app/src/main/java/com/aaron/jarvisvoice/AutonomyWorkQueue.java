package com.aaron.jarvisvoice;

import java.util.Objects;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Serial request work, with idempotent transport disposal entirely off the caller thread. */
final class AutonomyWorkQueue implements AutoCloseable {
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Runnable closeTransport;
    private volatile boolean closed;

    AutonomyWorkQueue(Runnable closeTransport) {
        this.closeTransport = Objects.requireNonNull(closeTransport);
    }

    boolean isClosed() { return closed; }

    synchronized boolean execute(Runnable operation) {
        if (closed) return false;
        executor.execute(() -> { if (!closed) operation.run(); });
        return true;
    }

    @Override public synchronized void close() {
        if (closed) return;
        closed = true;
        Thread cleanup = new Thread(() -> {
            executor.shutdownNow();
            closeTransport.run();
        }, "Jarvis-autonomy-cleanup");
        cleanup.setDaemon(true);
        cleanup.start();
    }
}
