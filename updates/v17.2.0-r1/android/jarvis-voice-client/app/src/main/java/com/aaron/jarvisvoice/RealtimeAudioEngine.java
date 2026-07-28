package com.aaron.jarvisvoice;

import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.media.audiofx.AcousticEchoCanceler;
import android.media.audiofx.AutomaticGainControl;
import android.media.audiofx.NoiseSuppressor;
import android.os.Process;

import java.util.Arrays;
import java.util.concurrent.atomic.AtomicBoolean;

public final class RealtimeAudioEngine {
    public interface Listener {
        void onAudioFrame(byte[] pcm16);
        void onInputLevel(float level);
        void onAudioError(String message);
    }

    public static final int SAMPLE_RATE = 24_000;
    public static final int FRAME_MILLIS = 20;
    public static final int FRAME_BYTES = AudioFrameSizer.bytesFor(SAMPLE_RATE, FRAME_MILLIS);

    private final Listener listener;
    private final AtomicBoolean running = new AtomicBoolean(false);
    private AudioRecord recorder;
    private AcousticEchoCanceler echoCanceler;
    private NoiseSuppressor noiseSuppressor;
    private AutomaticGainControl gainControl;
    private Thread captureThread;

    public RealtimeAudioEngine(Listener listener) {
        this.listener = listener;
    }

    public synchronized void start() {
        if (running.get()) return;
        try {
            int minimum = AudioRecord.getMinBufferSize(
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT
            );
            int bufferBytes = Math.max(minimum, FRAME_BYTES * 12);
            AudioFormat format = new AudioFormat.Builder()
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setSampleRate(SAMPLE_RATE)
                .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                .build();
            recorder = new AudioRecord.Builder()
                .setAudioSource(MediaRecorder.AudioSource.VOICE_COMMUNICATION)
                .setAudioFormat(format)
                .setBufferSizeInBytes(bufferBytes)
                .build();
            if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
                throw new IllegalStateException("Microphone could not be initialised at 24 kHz");
            }
            attachEffects(recorder.getAudioSessionId());
            recorder.startRecording();
            running.set(true);
            captureThread = new Thread(this::captureLoop, "jarvis-realtime-capture");
            captureThread.start();
        } catch (Exception exception) {
            releaseRecorder();
            listener.onAudioError("Microphone error: " + safeMessage(exception));
        }
    }

    public synchronized void stop() {
        running.set(false);
        AudioRecord current = recorder;
        if (current != null) {
            try {
                current.stop();
            } catch (Exception ignored) {}
        }
        Thread thread = captureThread;
        captureThread = null;
        if (thread != null && thread != Thread.currentThread()) {
            try {
                thread.join(500);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
        }
        releaseRecorder();
    }

    public boolean isRunning() {
        return running.get();
    }

    private void captureLoop() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_AUDIO);
        byte[] frame = new byte[FRAME_BYTES];
        int offset = 0;
        long lastLevelAt = 0L;
        try {
            while (running.get()) {
                AudioRecord current = recorder;
                if (current == null) return;
                int read = current.read(
                    frame,
                    offset,
                    frame.length - offset,
                    AudioRecord.READ_BLOCKING
                );
                if (read < 0) {
                    throw new IllegalStateException("AudioRecord read failed: " + read);
                }
                if (read == 0) continue;
                offset += read;
                if (offset < frame.length) continue;

                byte[] delivered = Arrays.copyOf(frame, frame.length);
                listener.onAudioFrame(delivered);
                long now = System.currentTimeMillis();
                if (now - lastLevelAt >= 100L) {
                    listener.onInputLevel(rms(delivered));
                    lastLevelAt = now;
                }
                offset = 0;
            }
        } catch (Exception exception) {
            if (running.getAndSet(false)) {
                listener.onAudioError("Microphone stopped: " + safeMessage(exception));
            }
        }
    }

    private void attachEffects(int sessionId) {
        try {
            if (AcousticEchoCanceler.isAvailable()) {
                echoCanceler = AcousticEchoCanceler.create(sessionId);
                if (echoCanceler != null) echoCanceler.setEnabled(true);
            }
        } catch (Exception ignored) {}
        try {
            if (NoiseSuppressor.isAvailable()) {
                noiseSuppressor = NoiseSuppressor.create(sessionId);
                if (noiseSuppressor != null) noiseSuppressor.setEnabled(true);
            }
        } catch (Exception ignored) {}
        try {
            if (AutomaticGainControl.isAvailable()) {
                gainControl = AutomaticGainControl.create(sessionId);
                if (gainControl != null) gainControl.setEnabled(true);
            }
        } catch (Exception ignored) {}
    }

    private synchronized void releaseRecorder() {
        releaseEffects();
        if (recorder != null) {
            try {
                recorder.release();
            } catch (Exception ignored) {}
            recorder = null;
        }
    }

    private void releaseEffects() {
        if (echoCanceler != null) {
            try { echoCanceler.release(); } catch (Exception ignored) {}
            echoCanceler = null;
        }
        if (noiseSuppressor != null) {
            try { noiseSuppressor.release(); } catch (Exception ignored) {}
            noiseSuppressor = null;
        }
        if (gainControl != null) {
            try { gainControl.release(); } catch (Exception ignored) {}
            gainControl = null;
        }
    }

    static float rms(byte[] pcm16) {
        if (pcm16 == null || pcm16.length < 2) return 0f;
        double sum = 0d;
        int samples = pcm16.length / 2;
        for (int i = 0; i + 1 < pcm16.length; i += 2) {
            int low = pcm16[i] & 0xff;
            int high = pcm16[i + 1];
            short sample = (short) ((high << 8) | low);
            double normalised = sample / 32768.0;
            sum += normalised * normalised;
        }
        return (float) Math.sqrt(sum / Math.max(1, samples));
    }

    private static String safeMessage(Exception exception) {
        String value = exception.getMessage();
        return value == null || value.isBlank() ? exception.getClass().getSimpleName() : value;
    }
}
