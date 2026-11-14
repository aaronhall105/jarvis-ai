package com.aaron.jarvisvoice;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.Test;
import static org.junit.Assert.*;

public class AutonomyWorkQueueTest {
    @Test(timeout = 5000) public void closeIsOffCallerThreadNonBlockingAndIdempotent() throws Exception {
        Thread caller = Thread.currentThread();
        AtomicReference<Thread> cleanupThread = new AtomicReference<>();
        AtomicInteger closes = new AtomicInteger();
        CountDownLatch started = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        CountDownLatch finished = new CountDownLatch(1);
        AutonomyWorkQueue queue = new AutonomyWorkQueue(() -> {
            cleanupThread.set(Thread.currentThread());
            closes.incrementAndGet();
            started.countDown();
            try { release.await(); }
            catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); }
            finally { finished.countDown(); }
        });
        try {
            queue.close();
            assertTrue(queue.isClosed());
            assertTrue(started.await(2, TimeUnit.SECONDS));
            assertNotSame(caller, cleanupThread.get());
            queue.close();
            assertFalse(queue.execute(() -> fail("Work accepted after close")));
        } finally {
            release.countDown();
            queue.close();
        }
        assertTrue(finished.await(2, TimeUnit.SECONDS));
        assertEquals(1, closes.get());
    }

    @Test(timeout = 5000) public void closeDiscardsQueuedOperationsAndInterruptsWorker() throws Exception {
        CountDownLatch started = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        CountDownLatch workerFinished = new CountDownLatch(1);
        CountDownLatch cleanupFinished = new CountDownLatch(1);
        AtomicInteger queuedRuns = new AtomicInteger();
        AutonomyWorkQueue queue = new AutonomyWorkQueue(cleanupFinished::countDown);
        try {
            assertTrue(queue.execute(() -> {
                started.countDown();
                try { release.await(); }
                catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); }
                finally { workerFinished.countDown(); }
            }));
            assertTrue(started.await(2, TimeUnit.SECONDS));
            assertTrue(queue.execute(queuedRuns::incrementAndGet));
            queue.close();
            assertTrue(cleanupFinished.await(2, TimeUnit.SECONDS));
            assertTrue(workerFinished.await(2, TimeUnit.SECONDS));
            assertEquals(0, queuedRuns.get());
        } finally {
            release.countDown();
            queue.close();
        }
    }
}
