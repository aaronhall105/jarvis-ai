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
        main.post(this::startInternal);
    }

    public void stop() {
        main.post(this::stopInternal);
    }

    public boolean isRunning() {
        return running;
    }

    private void startInternal() {
        stopInternal();
        running = true;

        try {
            if (SpeechRecognizer.isRecognitionAvailable(context)) {
                recognizer = SpeechRecognizer.createSpeechRecognizer(context);
            } else if (SpeechRecognizer.isOnDeviceRecognitionAvailable(context)) {
                recognizer = SpeechRecognizer.createOnDeviceSpeechRecognizer(context);
            } else {
                running = false;
                listener.onStandardError("No Android speech recogniser is available");
                return;
            }

            recognizer.setRecognitionListener(this);

            Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.UK.toLanguageTag())
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, Locale.UK.toLanguageTag())
                .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 5)
                .putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, false)
                .putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, context.getPackageName())
                .putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_MINIMUM_LENGTH_MILLIS, 350L)
                .putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 1050L)
                .putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 650L);

            recognizer.startListening(intent);
        } catch (Exception exception) {
            stopInternal();
            listener.onStandardError("Speech recognition could not start: " + safeMessage(exception));
        }
    }

    private void stopInternal() {
        running = false;
        SpeechRecognizer current = recognizer;
        recognizer = null;
        if (current != null) {
            try { current.cancel(); } catch (Exception ignored) {}
            try { current.destroy(); } catch (Exception ignored) {}
        }
    }

    private static String best(Bundle bundle) {
        ArrayList<String> results = bundle == null
            ? null
            : bundle.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
        if (results == null || results.isEmpty()) return "";

        float[] scores = bundle.getFloatArray(SpeechRecognizer.CONFIDENCE_SCORES);
        int selected = 0;
        if (scores != null && scores.length == results.size()) {
            float best = Float.NEGATIVE_INFINITY;
            for (int index = 0; index < scores.length; index++) {
                if (scores[index] > best) {
                    best = scores[index];
                    selected = index;
                }
            }
        }

        String value = results.get(selected);
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
            case SpeechRecognizer.ERROR_NO_MATCH -> "I did not catch that.";
            case SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "Listening timed out.";
            case SpeechRecognizer.ERROR_NETWORK,
                 SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "Speech recognition network error.";
            case SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "Speech recogniser is busy.";
            default -> "Speech recognition stopped (" + error + ").";
        };
        listener.onStandardError(message);
    }

    @Override public void onResults(Bundle results) {
        String text = best(results);
        stopInternal();
        if (text.isEmpty()) {
            listener.onStandardError("I did not catch that.");
        } else {
            listener.onStandardFinal(text);
        }
    }

    @Override public void onPartialResults(Bundle partialResults) {
        String text = best(partialResults);
        if (!text.isEmpty()) listener.onStandardPartial(text);
    }

    @Override public void onEvent(int eventType, Bundle params) {}

    private static String safeMessage(Exception exception) {
        String value = exception.getMessage();
        return value == null || value.isBlank()
            ? exception.getClass().getSimpleName()
            : value;
    }
}
