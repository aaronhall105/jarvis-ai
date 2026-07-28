package com.aaron.jarvisvoice;

import android.Manifest;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;

import java.util.ArrayList;
import java.util.Locale;

public final class WakePhraseEngine implements RecognitionListener {
    public interface Listener {
        void onWakePhrase(String transcript, String command);
        void onWakeStatus(String status);
        void onWakeError(String message);
    }

    private final Context context;
    private final Listener listener;
    private final Handler main = new Handler(Looper.getMainLooper());
    private SpeechRecognizer recognizer;
    private boolean running;
    private boolean listening;
    private boolean triggered;
    private boolean onDevice;
    private String wakePhrase = "jarvis";
    private int restartCount;

    public WakePhraseEngine(Context context, Listener listener) {
        this.context = context.getApplicationContext();
        this.listener = listener;
    }

    public void start(String configuredWakePhrase) {
        main.post(() -> {
            stopInternal();
            if (context.checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                    != PackageManager.PERMISSION_GRANTED) {
                listener.onWakeError("Microphone permission is required for the wake word");
                return;
            }

            wakePhrase = WakePhrasePolicy.normalise(configuredWakePhrase);
            if (wakePhrase.isEmpty()) wakePhrase = "jarvis";
            running = true;
            triggered = false;
            restartCount = 0;
            createRecognizer();
            listenSoon(120L);
        });
    }

    public void stop() {
        main.post(this::stopInternal);
    }

    public boolean isRunning() {
        return running;
    }

    private void stopInternal() {
        running = false;
        listening = false;
        triggered = false;
        restartCount = 0;
        main.removeCallbacksAndMessages(null);
        releaseRecognizer();
    }

    private void releaseRecognizer() {
        SpeechRecognizer current = recognizer;
        recognizer = null;
        if (current != null) {
            try { current.cancel(); } catch (Exception ignored) {}
            try { current.destroy(); } catch (Exception ignored) {}
        }
    }

    private void recreateRecognizer() {
        listening = false;
        releaseRecognizer();
        createRecognizer();
    }

    private void createRecognizer() {
        if (recognizer != null || !running) return;

        try {
            onDevice = SpeechRecognizer.isOnDeviceRecognitionAvailable(context);
            if (onDevice) {
                recognizer = SpeechRecognizer.createOnDeviceSpeechRecognizer(context);
            } else if (SpeechRecognizer.isRecognitionAvailable(context)) {
                recognizer = SpeechRecognizer.createSpeechRecognizer(context);
            } else {
                running = false;
                listener.onWakeError("No Android speech recogniser is available");
                return;
            }
            recognizer.setRecognitionListener(this);
            listener.onWakeStatus(
                onDevice
                    ? "Wake word ready on device — say \"" + wakePhrase + "\""
                    : "Wake word ready — say \"" + wakePhrase + "\""
            );
        } catch (Exception firstFailure) {
            releaseRecognizer();
            if (onDevice && SpeechRecognizer.isRecognitionAvailable(context)) {
                try {
                    onDevice = false;
                    recognizer = SpeechRecognizer.createSpeechRecognizer(context);
                    recognizer.setRecognitionListener(this);
                    listener.onWakeStatus("Wake word ready — say \"" + wakePhrase + "\"");
                    return;
                } catch (Exception ignored) {
                    releaseRecognizer();
                }
            }
            running = false;
            listener.onWakeError(
                "Wake recogniser unavailable: " + safeMessage(firstFailure)
            );
        }
    }

    private void listenSoon(long delayMillis) {
        if (!running || triggered) return;
        main.postDelayed(this::beginListening, delayMillis);
    }

    private void beginListening() {
        if (!running || listening || triggered) return;
        if (context.checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            stopInternal();
            listener.onWakeError("Microphone permission was removed");
            return;
        }

        createRecognizer();
        if (recognizer == null) {
            listenSoon(2_000L);
            return;
        }

        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(
                RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                RecognizerIntent.LANGUAGE_MODEL_FREE_FORM
            )
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.UK.toLanguageTag())
            .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 5)
            .putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, onDevice)
            .putExtra(
                RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS,
                850L
            )
            .putExtra(
                RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS,
                500L
            );

        try {
            listening = true;
            recognizer.startListening(intent);
        } catch (Exception exception) {
            listening = false;
            restartCount++;
            recreateRecognizer();
            listener.onWakeStatus("Wake word restarting");
            listenSoon(backoff(700L));
        }
    }

    private void evaluate(Bundle results) {
        ArrayList<String> values = results == null
            ? null
            : results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
        if (values == null || values.isEmpty() || triggered) return;

        for (String value : values) {
            WakePhrasePolicy.Decision decision =
                WakePhrasePolicy.evaluate(value, wakePhrase);
            if (!decision.triggered) continue;

            triggered = true;
            String transcript = value == null ? "" : value.trim();
            String command = decision.command;
            running = false;
            listening = false;
            main.removeCallbacksAndMessages(null);
            releaseRecognizer();
            listener.onWakePhrase(transcript, command);
            return;
        }
    }

    private long backoff(long base) {
        int multiplier = Math.min(6, Math.max(1, restartCount));
        return Math.min(6_000L, base * multiplier);
    }

    @Override public void onReadyForSpeech(Bundle params) {
        restartCount = 0;
        listener.onWakeStatus("Listening for \"" + wakePhrase + "\"");
    }

    @Override public void onBeginningOfSpeech() {}
    @Override public void onRmsChanged(float rmsdB) {}
    @Override public void onBufferReceived(byte[] buffer) {}
    @Override public void onEndOfSpeech() {
        listening = false;
    }

    @Override public void onError(int error) {
        listening = false;
        if (!running || triggered) return;

        if (error == SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS) {
            stopInternal();
            listener.onWakeError("Microphone permission is required for the wake word");
            return;
        }

        boolean recreate =
            error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY
                || error == SpeechRecognizer.ERROR_CLIENT
                || error == SpeechRecognizer.ERROR_SERVER_DISCONNECTED
                || error == SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE
                || error == SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED;

        restartCount++;
        if (recreate) recreateRecognizer();

        long baseDelay = switch (error) {
            case SpeechRecognizer.ERROR_NO_MATCH,
                 SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> 220L;
            case SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> 1_000L;
            case SpeechRecognizer.ERROR_TOO_MANY_REQUESTS -> 4_000L;
            case SpeechRecognizer.ERROR_SERVER_DISCONNECTED -> 2_000L;
            default -> 500L;
        };
        listenSoon(backoff(baseDelay));
    }

    @Override public void onResults(Bundle results) {
        listening = false;
        evaluate(results);
        if (running && !triggered) {
            restartCount = 0;
            listenSoon(180L);
        }
    }

    @Override public void onPartialResults(Bundle partialResults) {
        evaluate(partialResults);
    }

    @Override public void onEvent(int eventType, Bundle params) {}

    private static String safeMessage(Exception exception) {
        String value = exception.getMessage();
        return value == null || value.isBlank()
            ? exception.getClass().getSimpleName()
            : value;
    }
}
