package com.aaron.jarvisvoice.wear;

import android.content.Context;
import android.media.AudioAttributes;
import android.media.AudioFocusRequest;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioTrack;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import com.aaron.jarvisvoice.protocol.WearWireProtocol;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.atomic.AtomicLong;

/** Ordered, generation-safe watch playback that never blocks the UI thread. */
final class WearAudioPlayer {
    private static final String TAG = "JarvisWearPlayer";
    private final ExecutorService writer = Executors.newSingleThreadExecutor(r -> {
        Thread thread = new Thread(r, "jarvis-watch-speaker");
        thread.setDaemon(true);
        return thread;
    });
    private final Handler main = new Handler(Looper.getMainLooper());
    private final AtomicLong generation = new AtomicLong();
    private final Object trackLock = new Object();
    private AudioTrack track;
    private final AudioManager audioManager;
    private final AudioFocusRequest focusRequest;
    private long writtenFrames;
    private boolean firstWriteLogged;

    WearAudioPlayer(Context context) {
        audioManager = context.getSystemService(AudioManager.class);
        focusRequest = new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
            .setAudioAttributes(new AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ASSISTANT)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            .setOnAudioFocusChangeListener(change -> {
                if (change == AudioManager.AUDIOFOCUS_LOSS) interrupt();
            }).build();
    }

    void begin(long value) {
        interrupt();
        generation.set(value);
        firstWriteLogged = false;
    }

    void play(byte[] pcm, long frameGeneration) {
        if (pcm == null || pcm.length == 0 || generation.get() != frameGeneration) return;
        byte[] copy = pcm.clone();
        submit(() -> write(copy, frameGeneration));
    }

    void finish(long frameGeneration, Runnable completion) {
        submit(() -> {
            if (generation.get() != frameGeneration) return;
            AudioTrack current;
            long targetFrames;
            synchronized (trackLock) {
                current = track;
                targetFrames = writtenFrames;
            }
            if (current != null) {
                long waitUntil = System.currentTimeMillis() + 15_000L;
                while (generation.get() == frameGeneration
                        && Integer.toUnsignedLong(current.getPlaybackHeadPosition()) < targetFrames
                        && System.currentTimeMillis() < waitUntil) {
                    try {
                        Thread.sleep(10L);
                    } catch (InterruptedException interrupted) {
                        Thread.currentThread().interrupt();
                        return;
                    }
                }
            }
            if (generation.get() != frameGeneration) return;
            releaseTrack();
            main.post(() -> {
                if (generation.get() == frameGeneration) completion.run();
            });
        });
    }

    /** Flushes the hardware buffer synchronously so X is immediate. */
    void interrupt() {
        generation.incrementAndGet();
        releaseTrack();
    }

    void close() {
        interrupt();
        writer.shutdownNow();
    }

    private void write(byte[] pcm, long acceptedGeneration) {
        if (generation.get() != acceptedGeneration) return;
        try {
            AudioTrack current = ensureTrack();
            if (current.getPlayState() != AudioTrack.PLAYSTATE_PLAYING) {
                current.play();
                Log.i(TAG, "WATCH_LATENCY audiotrack_started generation=" + acceptedGeneration
                    + " elapsed_ms=" + SystemClock.elapsedRealtime());
            }
            int offset = 0;
            while (offset < pcm.length && generation.get() == acceptedGeneration) {
                int count = current.write(
                    pcm,
                    offset,
                    pcm.length - offset,
                    AudioTrack.WRITE_BLOCKING
                );
                if (count < 0) throw new IllegalStateException("Watch speaker write failed: " + count);
                if (count == 0) break;
                offset += count;
                if (!firstWriteLogged) {
                    firstWriteLogged = true;
                    Log.i(TAG, "WATCH_LATENCY first_audio_written generation=" + acceptedGeneration
                        + " bytes=" + count + " playState=" + current.getPlayState());
                }
                synchronized (trackLock) {
                    if (track == current) writtenFrames += count / 2L;
                }
            }
        } catch (Exception error) {
            Log.e(TAG, "audio_write_failed generation=" + acceptedGeneration, error);
            // Cancellation can legitimately release AudioTrack during a write.
        }
    }

    private AudioTrack ensureTrack() {
        synchronized (trackLock) {
            if (track != null) return track;
            int minimum = AudioTrack.getMinBufferSize(
                WearWireProtocol.SAMPLE_RATE,
                AudioFormat.CHANNEL_OUT_MONO,
                AudioFormat.ENCODING_PCM_16BIT
            );
            AudioTrack created = new AudioTrack.Builder()
                .setAudioAttributes(new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build())
                .setAudioFormat(new AudioFormat.Builder()
                    .setSampleRate(WearWireProtocol.SAMPLE_RATE)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build())
                .setBufferSizeInBytes(Math.max(minimum, 3840))
                .setTransferMode(AudioTrack.MODE_STREAM)
                .build();
            if (created.getState() != AudioTrack.STATE_INITIALIZED) {
                created.release();
                throw new IllegalStateException("Watch speaker unavailable");
            }
            writtenFrames = 0L;
            if (audioManager != null) {
                int focus = audioManager.requestAudioFocus(focusRequest);
                Log.i(TAG, "audio_focus_result=" + focus);
            }
            created.setVolume(1.0f);
            track = created;
            Log.i(TAG, "audiotrack_initialized sample_rate=" + created.getSampleRate()
                + " buffer_frames=" + created.getBufferSizeInFrames()
                + " session=" + created.getAudioSessionId());
            return created;
        }
    }

    private void releaseTrack() {
        AudioTrack current;
        synchronized (trackLock) {
            current = track;
            track = null;
            writtenFrames = 0L;
        }
        if (current == null) return;
        try { current.pause(); } catch (Exception ignored) {}
        try { current.flush(); } catch (Exception ignored) {}
        try { current.release(); } catch (Exception ignored) {}
        if (audioManager != null) audioManager.abandonAudioFocusRequest(focusRequest);
    }

    private void submit(Runnable action) {
        try {
            writer.execute(action);
        } catch (RejectedExecutionException ignored) {
            // Service is already closed.
        }
    }
}
