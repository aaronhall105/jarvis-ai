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

public final class StandardSpeechEngine implements RecognitionListener {
    public interface Listener {
        void onStandardReady();
        void onStandardPartial(String text);
        void onStandardFinal(String text);
        void onStandardError(String message);
    }

    private final Context context;
    private final Listener listener;
    private final Handler main = new Handler(Looper.getMainLooper());
    private SpeechRecognizer recognizer;
    private boolean running;

    public StandardSpeechEngine(Context context, Listener listener) {
        this.context = context.getApplicationContext();
        this.listener = listener;
    }

    public void start() {
        main.post(() -> {
            stopInternal();
            running = true;
            try {
                if (SpeechRecognizer.isOnDeviceRecognitionAvailable(context)) {
                    recognizer = SpeechRecognizer.createOnDeviceSpeechRecognizer(context);
                } else if (SpeechRecognizer.isRecognitionAvailable(context)) {
                    recognizer = SpeechRecognizer.createSpeechRecognizer(context);
                } else {
                    running = false;
                    listener.onStandardError("No Android speech recogniser is available");
                    return;
                }
                recognizer.setRecognitionListener(this);
                Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                    .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                    .putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.UK.toLanguageTag())
                    .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                    .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3)
                    .putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, false)
                    .putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 550L)
                    .putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 350L);
                recognizer.startListening(intent);
            } catch (Exception exception) {
                stopInternal();
                listener.onStandardError("Speech recognition could not start: " + safeMessage(exception));
            }
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
        if (recognizer != null) {
            try { recognizer.cancel(); } catch (Exception ignored) {}
            try { recognizer.destroy(); } catch (Exception ignored) {}
            recognizer = null;
        }
    }

    private static String first(Bundle bundle) {
        ArrayList<String> results = bundle == null
            ? null
            : bundle.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
        if (results == null || results.isEmpty()) return "";
        String value = results.get(0);
        return value == null ? "" : value.trim();
    }

    @Override public void onReadyForSpeech(Bundle params) {
        listener.onStandardReady();
    }

    @Override public void onBeginningOfSpeech() {}
    @Override public void onRmsChanged(float rmsdB) {}
    @Override public void onBufferReceived(byte[] buffer) {}
    @Override public void onEndOfSpeech() {}

    @Override public void onError(int error) {
        boolean wasRunning = running;
        stopInternal();
        if (!wasRunning) return;
        String message = switch (error) {
            case SpeechRecognizer.ERROR_NO_MATCH -> "I did not catch that. Tap the microphone and try again.";
            case SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "I did not hear anything.";
            case SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "Speech recognition network error.";
            case SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "Speech recogniser is busy. Try again in a moment.";
            default -> "Speech recognition stopped (" + error + ").";
        };
        listener.onStandardError(message);
    }

    @Override public void onResults(Bundle results) {
        String text = first(results);
        stopInternal();
        if (text.isEmpty()) {
            listener.onStandardError("I did not catch that. Tap the microphone and try again.");
        } else {
            listener.onStandardFinal(text);
        }
    }

    @Override public void onPartialResults(Bundle partialResults) {
        String text = first(partialResults);
        if (!text.isEmpty()) listener.onStandardPartial(text);
    }

    @Override public void onEvent(int eventType, Bundle params) {}

    private static String safeMessage(Exception exception) {
        String value = exception.getMessage();
        return value == null || value.isBlank() ? exception.getClass().getSimpleName() : value;
    }
}
