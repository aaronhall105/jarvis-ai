package com.aaron.jarvisvoice;

import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioTrack;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

public final class RealtimePlayback {
    public interface Listener {
        void onPlaybackState(boolean playing);
        void onPlaybackError(String message);
    }

    public static final int SAMPLE_RATE = 24_000;

    private final Listener listener;
    private final ExecutorService writer = Executors.newSingleThreadExecutor(r -> {
        Thread thread = new Thread(r, "jarvis-realtime-playback");
        thread.setDaemon(true);
        return thread;
    });
    private final AtomicBoolean closed = new AtomicBoolean(false);
    private final AtomicInteger generation = new AtomicInteger(0);
    private final Object trackLock = new Object();
    private AudioTrack track;
    private volatile boolean playing;

    public RealtimePlayback(Listener listener) {
        this.listener = listener;
    }

    public void enqueue(byte[] pcm16) {
        if (pcm16 == null || pcm16.length == 0 || closed.get()) return;
        byte[] copy = pcm16.clone();
        int acceptedGeneration = generation.get();
        try {
            writer.execute(() -> write(copy, acceptedGeneration));
        } catch (RejectedExecutionException ignored) {
            // Service is stopping.
        }
    }

    /** Flush immediately from the callback thread so barge-in is not queued behind audio writes. */
    public void interrupt() {
        if (closed.get()) return;
        generation.incrementAndGet();
        AudioTrack current;
        synchronized (trackLock) {
            current = track;
        }
        if (current != null) {
            try { current.pause(); } catch (Exception ignored) {}
            try { current.flush(); } catch (Exception ignored) {}
        }
        setPlaying(false);
    }

    public void markDone() {
        if (closed.get()) return;
        int acceptedGeneration = generation.get();
        try {
            writer.execute(() -> {
                if (
                    closed.get()
                        || generation.get() != acceptedGeneration
                ) {
                    return;
                }

                try {
                    Thread.sleep(180L);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    return;
                }

                if (
                    !closed.get()
                        && generation.get() == acceptedGeneration
                ) {
                    setPlaying(false);
                }
            });
        } catch (RejectedExecutionException ignored) {}
    }

    public boolean isPlaying() {
        return playing;
    }

    public void close() {
        if (!closed.compareAndSet(false, true)) return;
        generation.incrementAndGet();
        AudioTrack current;
        synchronized (trackLock) {
            current = track;
            track = null;
        }
        if (current != null) {
            try { current.pause(); } catch (Exception ignored) {}
            try { current.flush(); } catch (Exception ignored) {}
            try { current.release(); } catch (Exception ignored) {}
        }
        setPlaying(false);
        writer.shutdownNow();
    }

    private void write(byte[] pcm16, int acceptedGeneration) {
        if (closed.get() || generation.get() != acceptedGeneration) return;
        try {
            AudioTrack current = ensureTrack();
            if (current.getPlayState() != AudioTrack.PLAYSTATE_PLAYING) current.play();
            setPlaying(true);
            int offset = 0;
            while (offset < pcm16.length && !closed.get() && generation.get() == acceptedGeneration) {
                int written = current.write(
                    pcm16,
                    offset,
                    pcm16.length - offset,
                    AudioTrack.WRITE_BLOCKING
                );
                if (written < 0) throw new IllegalStateException("AudioTrack write failed: " + written);
                if (written == 0) break;
                offset += written;
            }
        } catch (Exception exception) {
            if (!closed.get()) listener.onPlaybackError("Speaker error: " + safeMessage(exception));
        }
    }

    private AudioTrack ensureTrack() {
        synchronized (trackLock) {
            if (track != null) return track;
            int minimum = AudioTrack.getMinBufferSize(
                SAMPLE_RATE,
                AudioFormat.CHANNEL_OUT_MONO,
                AudioFormat.ENCODING_PCM_16BIT
            );
            int bufferBytes = Math.max(minimum, AudioFrameSizer.bytesFor(SAMPLE_RATE, 160));
            AudioAttributes attributes = new AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                .build();
            AudioFormat format = new AudioFormat.Builder()
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setSampleRate(SAMPLE_RATE)
                .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                .build();
            AudioTrack created = new AudioTrack.Builder()
                .setAudioAttributes(attributes)
                .setAudioFormat(format)
                .setTransferMode(AudioTrack.MODE_STREAM)
                .setBufferSizeInBytes(bufferBytes)
                .build();
            if (created.getState() != AudioTrack.STATE_INITIALIZED) {
                try { created.release(); } catch (Exception ignored) {}
                throw new IllegalStateException("Speaker could not be initialised at 24 kHz");
            }
            track = created;
            return created;
        }
    }

    private void setPlaying(boolean value) {
        boolean changed = playing != value;
        playing = value;
        if (changed) listener.onPlaybackState(value);
    }

    private static String safeMessage(Exception exception) {
        String value = exception.getMessage();
        return value == null || value.isBlank() ? exception.getClass().getSimpleName() : value;
    }
}
