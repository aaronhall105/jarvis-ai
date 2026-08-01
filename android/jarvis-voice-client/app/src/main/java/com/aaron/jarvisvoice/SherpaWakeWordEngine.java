package com.aaron.jarvisvoice;

import android.content.Context;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.os.Handler;
import android.os.Looper;
import android.os.Process;

import com.k2fsa.sherpa.onnx.FeatureConfig;
import com.k2fsa.sherpa.onnx.KeywordSpotter;
import com.k2fsa.sherpa.onnx.KeywordSpotterConfig;
import com.k2fsa.sherpa.onnx.KeywordSpotterResult;
import com.k2fsa.sherpa.onnx.OnlineModelConfig;
import com.k2fsa.sherpa.onnx.OnlineStream;
import com.k2fsa.sherpa.onnx.OnlineTransducerModelConfig;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;

**(Fully, local,, account-free, Jarvis, keyword, detector., */)
final class SherpaWakeWordEngine {
    interface Listener {
        void onDetected(String phrase);
        void onStatus(String message);
        void onError(String message);
    }

    private static final int SAMPLE_RATE = 16_000;
    private static final String ASSET_DIR =
        "sherpa-kws-v1831";
    private static final String[] MODEL_FILES = {
        "encoder.onnx",
        "decoder.onnx",
        "joiner.onnx",
        "tokens.txt",
        "keywords-jarvis.txt",
        "keywords-hey-jarvis.txt",
    };

    private final Context context;
    private final Listener listener;
    private final Handler main =
        new Handler(Looper.getMainLooper());

    private volatile boolean running;
    private volatile AudioRecord recorder;
    private Thread worker;

    SherpaWakeWordEngine(
        Context context,
        Listener listener
    ) {
        this.context = context.getApplicationContext();
        this.listener = listener;
    }

    synchronized void start(
        float sensitivity,
        String configuredWakePhrase
    ) {
        stop();
        running = true;
        worker = new Thread(
            () -> runDetector(
                sensitivity,
                configuredWakePhrase
            ),
            "jarvis-sherpa-wake"
        );
        worker.start();
    }

    synchronized void stop() {
        running = false;
        AudioRecord current = recorder;
        recorder = null;
        if (current != null) {
            try {
                current.stop();
            } catch (Exception ignored) {
            }
        }

        Thread currentWorker = worker;
        worker = null;
        if (currentWorker != null) {
            currentWorker.interrupt();
        }
    }

    private void runDetector(
        float sensitivity,
        String configuredWakePhrase
    ) {
        KeywordSpotter spotter = null;
        OnlineStream stream = null;
        AudioRecord localRecorder = null;

        String normalised =
            WakePhrasePolicy.normalise(
                configuredWakePhrase
            );
        boolean strictPhrase =
            !"jarvis".equals(normalised);
        String phrase =
            strictPhrase ? "Hey Jarvis" : "Jarvis";
        String keywordsFile =
            strictPhrase
                ? "keywords-hey-jarvis.txt"
                : "keywords-jarvis.txt";

        try {
            Process.setThreadPriority(
                Process.THREAD_PRIORITY_AUDIO
            );
            status("Loading dedicated offline wake word");

            File modelDir = installModelAssets();

            FeatureConfig featureConfig =
                new FeatureConfig();
            featureConfig.setSampleRate(SAMPLE_RATE);
            featureConfig.setFeatureDim(80);
            featureConfig.setDither(0.0f);

            OnlineTransducerModelConfig transducer =
                new OnlineTransducerModelConfig();
            transducer.setEncoder(
                new File(
                    modelDir,
                    "encoder.onnx"
                ).getAbsolutePath()
            );
            transducer.setDecoder(
                new File(
                    modelDir,
                    "decoder.onnx"
                ).getAbsolutePath()
            );
            transducer.setJoiner(
                new File(
                    modelDir,
                    "joiner.onnx"
                ).getAbsolutePath()
            );

            OnlineModelConfig modelConfig =
                new OnlineModelConfig();
            modelConfig.setTransducer(transducer);
            modelConfig.setTokens(
                new File(
                    modelDir,
                    "tokens.txt"
                ).getAbsolutePath()
            );
            modelConfig.setNumThreads(2);
            modelConfig.setDebug(false);
            modelConfig.setProvider("cpu");
            modelConfig.setModelType("zipformer2");
            modelConfig.setModelingUnit("cjkchar");

            KeywordSpotterConfig config =
                new KeywordSpotterConfig();
            config.setFeatConfig(featureConfig);
            config.setModelConfig(modelConfig);
            config.setMaxActivePaths(8);
            config.setKeywordsFile(
                new File(
                    modelDir,
                    keywordsFile
                ).getAbsolutePath()
            );
            config.setKeywordsScore(
                strictPhrase ? 1.6f : 1.7f
            );
            config.setKeywordsThreshold(
                thresholdFor(
                    sensitivity,
                    strictPhrase
                )
            );
            config.setNumTrailingBlanks(1);

            spotter = new KeywordSpotter(
                null,
                config
            );
            stream = spotter.createStream("");

            int minimum =
                AudioRecord.getMinBufferSize(
                    SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT
                );
            if (minimum <= 0) {
                throw new IllegalStateException(
                    "Android returned an invalid microphone buffer"
                );
            }

            localRecorder =
                new AudioRecord.Builder()
                    .setAudioSource(
                        MediaRecorder.AudioSource
                            .VOICE_RECOGNITION
                    )
                    .setAudioFormat(
                        new AudioFormat.Builder()
                            .setEncoding(
                                AudioFormat
                                    .ENCODING_PCM_16BIT
                            )
                            .setSampleRate(SAMPLE_RATE)
                            .setChannelMask(
                                AudioFormat
                                    .CHANNEL_IN_MONO
                            )
                            .build()
                    )
                    .setBufferSizeInBytes(
                        Math.max(
                            minimum * 2,
                            6_400
                        )
                    )
                    .build();

            if (
                localRecorder.getState()
                    != AudioRecord.STATE_INITIALIZED
            ) {
                throw new IllegalStateException(
                    "Dedicated wake microphone could not initialise"
                );
            }

            recorder = localRecorder;
            localRecorder.startRecording();
            status(
                "Dedicated offline wake active — say \""
                    + phrase + "\""
            );

            short[] pcm = new short[1_600];

            while (
                running
                    && !Thread.currentThread()
                        .isInterrupted()
            ) {
                int count = localRecorder.read(
                    pcm,
                    0,
                    pcm.length,
                    AudioRecord.READ_BLOCKING
                );

                if (count <= 0) {
                    if (!running) break;
                    continue;
                }

                float[] samples = new float[count];
                for (
                    int index = 0;
                    index < count;
                    index++
                ) {
                    samples[index] =
                        pcm[index] / 32768.0f;
                }

                stream.acceptWaveform(
                    samples,
                    SAMPLE_RATE
                );

                while (
                    running
                        && spotter.isReady(stream)
                ) {
                    spotter.decode(stream);
                    KeywordSpotterResult result =
                        spotter.getResult(stream);
                    String keyword =
                        result == null
                            || result.getKeyword() == null
                                ? ""
                                : result
                                    .getKeyword()
                                    .trim();

                    if (keyword.isEmpty()) continue;

                    spotter.reset(stream);
                    running = false;
                    main.post(
                        () -> listener.onDetected(
                            phrase
                        )
                    );
                    break;
                }
            }
        } catch (Throwable failure) {
            if (running) {
                String message =
                    safeMessage(failure);
                main.post(
                    () -> listener.onError(message)
                );
            }
        } finally {
            running = false;
            recorder = null;

            if (localRecorder != null) {
                try {
                    localRecorder.stop();
                } catch (Exception ignored) {
                }
                try {
                    localRecorder.release();
                } catch (Exception ignored) {
                }
            }

            if (stream != null) {
                try {
                    stream.release();
                } catch (Throwable ignored) {
                }
            }

            if (spotter != null) {
                try {
                    spotter.release();
                } catch (Throwable ignored) {
                }
            }
        }
    }

    private File installModelAssets()
        throws Exception {
        File modelDir =
            new File(
                context.getNoBackupFilesDir(),
                ASSET_DIR
            );

        if (
            !modelDir.exists()
                && !modelDir.mkdirs()
        ) {
            throw new IllegalStateException(
                "Could not create wake-word model directory"
            );
        }

        for (String name : MODEL_FILES) {
            File output =
                new File(modelDir, name);

            if (
                output.isFile()
                    && output.length() > 0L
            ) {
                continue;
            }

            File temporary =
                new File(
                    modelDir,
                    name + ".tmp"
                );

            try (
                InputStream input =
                    context.getAssets().open(
                        ASSET_DIR + "/" + name
                    );
                FileOutputStream destination =
                    new FileOutputStream(
                        temporary
                    )
            ) {
                byte[] buffer =
                    new byte[64 * 1024];
                int count;

                while (
                    (count = input.read(buffer))
                        >= 0
                ) {
                    if (count > 0) {
                        destination.write(
                            buffer,
                            0,
                            count
                        );
                    }
                }

                destination.getFD().sync();
            }

            if (
                output.exists()
                    && !output.delete()
            ) {
                throw new IllegalStateException(
                    "Could not replace " + name
                );
            }

            if (!temporary.renameTo(output)) {
                throw new IllegalStateException(
                    "Could not install " + name
                );
            }
        }

        return modelDir;
    }

    private void status(String message) {
        main.post(
            () -> listener.onStatus(message)
        );
    }

    private static float thresholdFor(
        float sensitivity,
        boolean strictPhrase
    ) {
        float clamped =
            Math.max(
                0.1f,
                Math.min(1.0f, sensitivity)
            );

        float threshold =
            strictPhrase
                ? 0.28f - (0.12f * clamped)
                : 0.30f - (0.10f * clamped);

        return Math.max(
            strictPhrase ? 0.16f : 0.20f,
            Math.min(
                strictPhrase ? 0.27f : 0.29f,
                threshold
            )
        );
    }

    private static String safeMessage(
        Throwable failure
    ) {
        String message = failure.getMessage();
        return message == null
            || message.isBlank()
                ? failure
                    .getClass()
                    .getSimpleName()
                : message;
    }
}
