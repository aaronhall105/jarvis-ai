package com.aaron.jarvisvoice;

import android.content.Context;
import android.media.AudioAttributes;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;

import java.util.Locale;

final class ReliableSpeechFallback implements TextToSpeech.OnInitListener {
    interface Listener {
        void onFallbackStarted();
        void onFallbackDone();
        void onFallbackError(String message);
    }

    private static final int MAX_READY_RETRIES = 12;

    private final Context context;
    private final Listener listener;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Object lock = new Object();

    private TextToSpeech engine;
    private boolean ready;
    private boolean closed;
    private boolean pending;
    private boolean speaking;
    private int generation;
    private String currentUtterance = "";
    private String initialisationError = "";

    ReliableSpeechFallback(Context context, Listener listener) {
        this.context = context.getApplicationContext();
        this.listener = listener;
        main.post(() -> {
            TextToSpeech created = new TextToSpeech(this.context, this);
            synchronized (lock) {
                if (closed) {
                    created.shutdown();
                    return;
                }
                engine = created;
            }
        });
    }

    @Override public void onInit(int status) {
        main.post(() -> finishInitialisation(status, 0));
    }

    private void finishInitialisation(int status, int attempt) {
        final TextToSpeech current;
        synchronized (lock) {
            if (closed) return;
            current = engine;
        }

        if (current == null && attempt < MAX_READY_RETRIES) {
            main.postDelayed(
                () -> finishInitialisation(status, attempt + 1),
                50L
            );
            return;
        }

        if (current == null || status != TextToSpeech.SUCCESS) {
            synchronized (lock) {
                initialisationError =
                    "Android speech engine could not initialise";
            }
            return;
        }

        try {
            current.setLanguage(Locale.UK);
            current.setSpeechRate(1.04f);
            current.setPitch(0.96f);
            current.setAudioAttributes(
                new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            );
            current.setOnUtteranceProgressListener(
                new UtteranceProgressListener() {
                    @Override public void onStart(String utteranceId) {
                        if (!isCurrent(utteranceId)) return;
                        synchronized (lock) {
                            pending = false;
                            speaking = true;
                        }
                        main.post(listener::onFallbackStarted);
                    }

                    @Override public void onDone(String utteranceId) {
                        complete(utteranceId, null);
                    }

                    @Override public void onError(String utteranceId) {
                        complete(
                            utteranceId,
                            "Android speech fallback failed"
                        );
                    }

                    @Override public void onError(
                        String utteranceId,
                        int errorCode
                    ) {
                        complete(
                            utteranceId,
                            "Android speech fallback error " + errorCode
                        );
                    }
                }
            );
            synchronized (lock) {
                ready = true;
                initialisationError = "";
            }
        } catch (Exception exception) {
            synchronized (lock) {
                initialisationError =
                    "Android speech setup failed: "
                        + safeMessage(exception);
            }
        }
    }

    void schedule(String text, long delayMillis) {
        String value = text == null ? "" : text.trim();
        if (value.isEmpty()) return;

        final int acceptedGeneration;
        synchronized (lock) {
            if (closed) return;
            generation++;
            acceptedGeneration = generation;
            pending = true;
            speaking = false;
            currentUtterance = "";
        }

        main.postDelayed(
            () -> speakWhenReady(value, acceptedGeneration, 0),
            Math.max(0L, delayMillis)
        );
    }

    void cancel() {
        final TextToSpeech current;
        synchronized (lock) {
            generation++;
            pending = false;
            speaking = false;
            currentUtterance = "";
            current = engine;
        }
        if (current != null) {
            try {
                current.stop();
            } catch (Exception ignored) {}
        }
    }

    boolean isPendingOrSpeaking() {
        synchronized (lock) {
            return pending || speaking;
        }
    }

    boolean isSpeaking() {
        synchronized (lock) {
            return speaking;
        }
    }

    void shutdown() {
        final TextToSpeech current;
        synchronized (lock) {
            if (closed) return;
            closed = true;
            generation++;
            pending = false;
            speaking = false;
            ready = false;
            currentUtterance = "";
            current = engine;
            engine = null;
        }
        main.removeCallbacksAndMessages(null);
        if (current != null) {
            try {
                current.stop();
            } catch (Exception ignored) {}
            try {
                current.shutdown();
            } catch (Exception ignored) {}
        }
    }

    private void speakWhenReady(
        String text,
        int acceptedGeneration,
        int attempt
    ) {
        final TextToSpeech current;
        final boolean currentlyReady;

        synchronized (lock) {
            if (
                closed
                    || acceptedGeneration != generation
                    || !pending
            ) {
                return;
            }
            current = engine;
            currentlyReady = ready;
        }

        if (
            (current == null || !currentlyReady)
                && attempt < MAX_READY_RETRIES
        ) {
            main.postDelayed(
                () -> speakWhenReady(
                    text,
                    acceptedGeneration,
                    attempt + 1
                ),
                250L
            );
            return;
        }

        if (current == null || !currentlyReady) {
            String failure;
            synchronized (lock) {
                if (acceptedGeneration == generation) pending = false;
                failure = initialisationError.isBlank()
                    ? "Android speech fallback is not ready"
                    : initialisationError;
            }
            notifyError(failure);
            return;
        }

        String utteranceId =
            "jarvis-fallback-" + acceptedGeneration + "-"
                + System.nanoTime();

        synchronized (lock) {
            if (
                closed
                    || acceptedGeneration != generation
                    || !pending
            ) {
                return;
            }
            currentUtterance = utteranceId;
        }

        Bundle parameters = new Bundle();
        int result = current.speak(
            text,
            TextToSpeech.QUEUE_FLUSH,
            parameters,
            utteranceId
        );

        if (result == TextToSpeech.ERROR) {
            synchronized (lock) {
                if (utteranceId.equals(currentUtterance)) {
                    pending = false;
                    speaking = false;
                    currentUtterance = "";
                }
            }
            notifyError("Android speech fallback could not start");
        }
    }

    private boolean isCurrent(String utteranceId) {
        synchronized (lock) {
            return !closed
                && utteranceId != null
                && utteranceId.equals(currentUtterance);
        }
    }

    private void complete(String utteranceId, String error) {
        synchronized (lock) {
            if (
                closed
                    || utteranceId == null
                    || !utteranceId.equals(currentUtterance)
            ) {
                return;
            }
            pending = false;
            speaking = false;
            currentUtterance = "";
        }

        if (error == null) {
            main.post(listener::onFallbackDone);
        } else {
            main.post(() -> listener.onFallbackError(error));
        }
    }

    private void notifyError(String message) {
        main.post(() -> listener.onFallbackError(message));
    }

    private static String safeMessage(Exception exception) {
        String message = exception.getMessage();
        return message == null || message.isBlank()
            ? exception.getClass().getSimpleName()
            : message;
    }
}
