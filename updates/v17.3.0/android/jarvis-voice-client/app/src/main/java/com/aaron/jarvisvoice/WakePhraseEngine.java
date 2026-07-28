package com.aaron.jarvisvoice;

import android.content.Context;
import android.content.Intent;
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
    private String wakePhrase = "jarvis";

    public WakePhraseEngine(Context context, Listener listener) {
        this.context = context.getApplicationContext();
        this.listener = listener;
    }

    public void start(String configuredWakePhrase) {
        main.post(() -> {
            stopInternal();
            wakePhrase = WakePhrasePolicy.normalise(configuredWakePhrase);
            if (wakePhrase.isEmpty()) wakePhrase = "jarvis";
            running = true;
            triggered = false;
            createRecognizer();
            listenSoon(100);
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
        main.removeCallbacksAndMessages(null);
        if (recognizer != null) {
            try { recognizer.cancel(); } catch (Exception ignored) {}
            try { recognizer.destroy(); } catch (Exception ignored) {}
            recognizer = null;
        }
    }

    private void createRecognizer() {
        if (recognizer != null || !running) return;
        try {
            if (SpeechRecognizer.isOnDeviceRecognitionAvailable(context)) {
                recognizer = SpeechRecognizer.createOnDeviceSpeechRecognizer(context);
                listener.onWakeStatus("Wake word armed on-device — say “" + wakePhrase + "”");
            } else if (SpeechRecognizer.isRecognitionAvailable(context)) {
                recognizer = SpeechRecognizer.createSpeechRecognizer(context);
                listener.onWakeStatus("Wake word armed — say “" + wakePhrase + "”");
            } else {
                listener.onWakeError("No Android speech recogniser is available for wake-word mode");
                return;
            }
            recognizer.setRecognitionListener(this);
        } catch (Exception exception) {
            recognizer = null;
            listener.onWakeError("Wake recogniser unavailable: " + safeMessage(exception));
        }
    }

    private void listenSoon(long delayMillis) {
        if (!running || triggered) return;
        main.postDelayed(this::beginListening, delayMillis);
    }

    private void beginListening() {
        if (!running || listening || triggered) return;
        createRecognizer();
        if (recognizer == null) {
            listenSoon(2_000);
            return;
        }
        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.UK.toLanguageTag())
            .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 5)
            .putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
            .putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 900L)
            .putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 600L);
        try {
            listening = true;
            recognizer.startListening(intent);
        } catch (Exception exception) {
            listening = false;
            listener.onWakeStatus("Wake recogniser restarting");
            listenSoon(900);
        }
    }

    private void evaluate(Bundle results) {
        ArrayList<String> values = results == null
            ? null
            : results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
        if (values == null || values.isEmpty() || triggered) return;
        for (String value : values) {
            WakePhrasePolicy.Decision decision = WakePhrasePolicy.evaluate(value, wakePhrase);
            if (!decision.triggered) continue;
            triggered = true;
            String transcript = value == null ? "" : value.trim();
            String command = decision.command;
            stopInternal();
            listener.onWakePhrase(transcript, command);
            return;
        }
    }

    @Override public void onReadyForSpeech(Bundle params) {
        listener.onWakeStatus("Sleeping lightly — say “" + wakePhrase + "”");
    }
    @Override public void onBeginningOfSpeech() {}
    @Override public void onRmsChanged(float rmsdB) {}
    @Override public void onBufferReceived(byte[] buffer) {}
    @Override public void onEndOfSpeech() { listening = false; }

    @Override public void onError(int error) {
        listening = false;
        if (!running || triggered) return;
        long delay = switch (error) {
            case SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> 1_200L;
            case SpeechRecognizer.ERROR_TOO_MANY_REQUESTS -> 5_000L;
            case SpeechRecognizer.ERROR_SERVER_DISCONNECTED -> 2_500L;
            default -> 350L;
        };
        listenSoon(delay);
    }

    @Override public void onResults(Bundle results) {
        listening = false;
        evaluate(results);
        if (running && !triggered) listenSoon(180);
    }

    @Override public void onPartialResults(Bundle partialResults) {
        evaluate(partialResults);
    }

    @Override public void onEvent(int eventType, Bundle params) {}

    private static String safeMessage(Exception exception) {
        String value = exception.getMessage();
        return value == null || value.isBlank() ? exception.getClass().getSimpleName() : value;
    }
}
