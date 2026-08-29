package com.aaron.jarvisvoice.protocol;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;

/** Bounded, generation-owned PCM queue used only while a Wear channel is opening. */
public final class StartupAudioBuffer {
    private final int maximumBytes;
    private final ArrayDeque<byte[]> frames = new ArrayDeque<>();
    private long generation;
    private int bytes;

    public StartupAudioBuffer(int maximumBytes) {
        if (maximumBytes <= 0) throw new IllegalArgumentException("maximumBytes must be positive");
        this.maximumBytes = maximumBytes;
    }

    public synchronized void begin(long value) {
        clear();
        generation = value;
    }

    public synchronized boolean offer(long value, byte[] pcm) {
        if (value != generation || pcm == null || pcm.length == 0) return false;
        byte[] copy = pcm.clone();
        while (!frames.isEmpty() && bytes + copy.length > maximumBytes) {
            bytes -= frames.removeFirst().length;
        }
        if (copy.length > maximumBytes) {
            int offset = copy.length - maximumBytes;
            byte[] tail = new byte[maximumBytes];
            System.arraycopy(copy, offset, tail, 0, maximumBytes);
            copy = tail;
        }
        frames.addLast(copy);
        bytes += copy.length;
        return true;
    }

    public synchronized List<byte[]> drain(long value) {
        if (value != generation) return List.of();
        List<byte[]> result = new ArrayList<>(frames);
        clearFrames();
        return result;
    }

    public synchronized void cancel(long value) {
        if (value == generation) {
            clear();
            generation = 0L;
        }
    }

    public synchronized void reset() {
        clear();
        generation = 0L;
    }

    public synchronized int sizeBytes() { return bytes; }

    private void clear() { clearFrames(); }
    private void clearFrames() { frames.clear(); bytes = 0; }
}
