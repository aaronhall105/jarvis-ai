package com.aaron.jarvisvoice.protocol;

import java.util.Objects;
import java.util.concurrent.atomic.AtomicLong;

/** Platform-neutral session lifecycle. Every callback is scoped to one session generation. */
public final class WatchConversationMachine {
    public static final long DEFAULT_INACTIVITY_TIMEOUT_MS = 60_000L;
    private static final AtomicLong NEXT_GENERATION = new AtomicLong(
        Math.max(1L, System.nanoTime())
    );

    private WatchConversationState state = WatchConversationState.IDLE;
    private long generation;
    private final long inactivityTimeoutMs;

    public WatchConversationMachine() {
        this(DEFAULT_INACTIVITY_TIMEOUT_MS);
    }

    public WatchConversationMachine(long inactivityTimeoutMs) {
        if (inactivityTimeoutMs <= 0) throw new IllegalArgumentException("timeout must be positive");
        this.inactivityTimeoutMs = inactivityTimeoutMs;
    }

    public synchronized long start() {
        if (state != WatchConversationState.IDLE) return generation;
        generation = NEXT_GENERATION.updateAndGet(value -> value == Long.MAX_VALUE ? 1L : value + 1L);
        state = WatchConversationState.LISTENING;
        return generation;
    }

    public synchronized boolean processing(long frameGeneration) {
        if (accepts(frameGeneration) && state == WatchConversationState.PROCESSING) return true;
        return transition(frameGeneration, WatchConversationState.LISTENING, WatchConversationState.PROCESSING)
            || transition(frameGeneration, WatchConversationState.FOLLOW_UP, WatchConversationState.PROCESSING);
    }

    public synchronized boolean speaking(long frameGeneration) {
        if (!accepts(frameGeneration)) return false;
        if (state == WatchConversationState.SPEAKING) return true;
        if (state == WatchConversationState.PROCESSING
                || state == WatchConversationState.LISTENING
                || state == WatchConversationState.FOLLOW_UP) {
            state = WatchConversationState.SPEAKING;
            return true;
        }
        return false;
    }

    public synchronized boolean playbackComplete(long frameGeneration) {
        if (!accepts(frameGeneration) || state != WatchConversationState.SPEAKING) return false;
        state = WatchConversationState.FOLLOW_UP;
        state = WatchConversationState.LISTENING;
        return true;
    }

    public synchronized void end() {
        if (state == WatchConversationState.IDLE) return;
        state = WatchConversationState.ENDING;
        state = WatchConversationState.IDLE;
    }

    public synchronized void disconnect() { end(); }
    public synchronized void inactivityTimeout() { end(); }
    public synchronized boolean accepts(long frameGeneration) {
        return state != WatchConversationState.IDLE && frameGeneration == generation;
    }
    public synchronized WatchConversationState state() { return state; }
    public synchronized long generation() { return generation; }
    public long inactivityTimeoutMs() { return inactivityTimeoutMs; }

    private boolean transition(long frameGeneration, WatchConversationState from, WatchConversationState to) {
        if (!accepts(frameGeneration) || !Objects.equals(state, from)) return false;
        state = to;
        return true;
    }
}
