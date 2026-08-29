package com.aaron.jarvisvoice;

import android.content.Context;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.Process;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;

public final class RealtimeAudioEngine {
    public interface Listener {
        default void onAudioReady() {}
        void onAudioFrame(byte[] pcm16);
        void onInputLevel(float level);
        void onAudioError(String message);
    }

    private static final int SAMPLE_RATE = 24_000;
    private static final int FRAME_SAMPLES = 480;

    private final Context context;
    private final Listener listener;

    private volatile boolean running;
    private volatile boolean captureReady;
    private volatile AudioRecord recorder;
    private volatile Thread worker;
    private volatile int captureGeneration;

    public RealtimeAudioEngine(Listener listener) {
        if (!(listener instanceof Context)) {
            throw new IllegalArgumentException(
                "RealtimeAudioEngine listener must be an Android Context"
            );
        }
        this.context =
            ((Context) listener).getApplicationContext();
        this.listener = listener;
    }

    public RealtimeAudioEngine(
        Context context,
        Listener listener
    ) {
        this.context = context.getApplicationContext();
        this.listener = listener;
    }

    public synchronized void start() {
        if (running) return;

        int generation = ++captureGeneration;
        running = true;
        captureReady = false;

        Thread next = new Thread(
            () -> captureLoop(generation),
            "jarvis-live-audio"
        );
        worker = next;
        next.start();
    }

    public synchronized void stop() {
        /*
         * Invalidate this generation before touching the recorder.
         * An old worker can therefore never publish callbacks or clear
         * the state of a replacement capture.
         */
        ++captureGeneration;
        running = false;
        captureReady = false;

        AudioRecord current = recorder;
        recorder = null;

        if (current != null) {
            try {
                current.stop();
            } catch (Throwable ignored) {}
        }

        Thread currentWorker = worker;
        worker = null;

        if (currentWorker != null) {
            currentWorker.interrupt();
        }
    }

    public boolean isRunning() {
        return running;
    }

    public boolean isCaptureReady() {
        return captureReady;
    }

    private boolean owns(int generation) {
        return CaptureEpochPolicy.mayPublish(
            running,
            generation,
            captureGeneration
        );
    }

    private void captureLoop(int generation) {
        AudioRecord localRecorder = null;
        AudioInputEffects localEffects = null;

        try {
            Process.setThreadPriority(
                Process.THREAD_PRIORITY_AUDIO
            );

            int minimum = AudioRecord.getMinBufferSize(
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT
            );

            if (minimum <= 0) {
                throw new IllegalStateException(
                    "Android returned an invalid live microphone buffer"
                );
            }

            int bufferBytes = Math.max(
                minimum * 2,
                FRAME_SAMPLES * 2 * 8
            );

            localRecorder = new AudioRecord.Builder()
                .setAudioSource(
                    MediaRecorder.AudioSource.VOICE_COMMUNICATION
                )
                .setAudioFormat(
                    new AudioFormat.Builder()
                        .setEncoding(
                            AudioFormat.ENCODING_PCM_16BIT
                        )
                        .setSampleRate(SAMPLE_RATE)
                        .setChannelMask(
                            AudioFormat.CHANNEL_IN_MONO
                        )
                        .build()
                )
                .setBufferSizeInBytes(bufferBytes)
                .build();

            if (
                localRecorder.getState()
                    != AudioRecord.STATE_INITIALIZED
            ) {
                throw new IllegalStateException(
                    "Live microphone could not initialise"
                );
            }

            if (!owns(generation)) {
                return;
            }

            localEffects =
                AudioInputEffects.attach(localRecorder);

            new VoiceDiagnosticsStore(context)
                .recordAudioProcessing(
                    localEffects.summary()
                );

            if (!owns(generation)) {
                return;
            }

            recorder = localRecorder;
            localRecorder.startRecording();

            if (
                localRecorder.getRecordingState()
                    != AudioRecord.RECORDSTATE_RECORDING
            ) {
                throw new IllegalStateException(
                    "Live microphone did not enter recording state"
                );
            }

            if (!owns(generation)) {
                return;
            }

            captureReady = true;

            /*
             * This is the authoritative capture-ready boundary.
             * VoiceService may start its no-speech timeout only now.
             */
            listener.onAudioReady();

            short[] frame =
                new short[FRAME_SAMPLES];

            while (
                owns(generation)
                    && !Thread.currentThread().isInterrupted()
            ) {
                int count = localRecorder.read(
                    frame,
                    0,
                    frame.length,
                    AudioRecord.READ_BLOCKING
                );

                if (count <= 0) {
                    if (!owns(generation)) {
                        break;
                    }
                    continue;
                }

                if (!owns(generation)) {
                    break;
                }

                byte[] pcm =
                    shortsToLittleEndian(
                        frame,
                        count
                    );

                listener.onInputLevel(
                    level(frame, count)
                );

                if (!owns(generation)) {
                    break;
                }

                listener.onAudioFrame(pcm);
            }

        } catch (SecurityException denied) {
            if (owns(generation)) {
                listener.onAudioError(
                    "Microphone permission is required"
                );
            }

        } catch (Throwable failure) {
            if (owns(generation)) {
                listener.onAudioError(
                    "Live microphone failed: "
                        + safeMessage(failure)
                );
            }

        } finally {
            /*
             * Critically, a stale worker is forbidden from clearing
             * state belonging to the replacement generation.
             */
            if (owns(generation)) {
                running = false;
                captureReady = false;

                if (recorder == localRecorder) {
                    recorder = null;
                }

                if (worker == Thread.currentThread()) {
                    worker = null;
                }
            }

            if (localRecorder != null) {
                try {
                    localRecorder.stop();
                } catch (Throwable ignored) {}
            }

            if (localEffects != null) {
                localEffects.release();
            }

            if (localRecorder != null) {
                try {
                    localRecorder.release();
                } catch (Throwable ignored) {}
            }
        }
    }

    private static byte[] shortsToLittleEndian(
        short[] samples,
        int count
    ) {
        ByteBuffer buffer = ByteBuffer
            .allocate(count * 2)
            .order(ByteOrder.LITTLE_ENDIAN);

        for (int index = 0; index < count; index++) {
            buffer.putShort(samples[index]);
        }

        return buffer.array();
    }

    private static float level(
        short[] samples,
        int count
    ) {
        if (count <= 0) return 0f;

        double squares = 0.0;

        for (int index = 0; index < count; index++) {
            double sample =
                samples[index] / 32768.0;
            squares += sample * sample;
        }

        double rms =
            Math.sqrt(squares / count);

        return (float) Math.max(
            0.0,
            Math.min(1.0, rms * 5.0)
        );
    }

    private static String safeMessage(
        Throwable failure
    ) {
        String message = failure.getMessage();

        return message == null || message.isBlank()
            ? failure.getClass().getSimpleName()
            : message;
    }
}
