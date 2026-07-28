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

public final class SpeechLoop implements RecognitionListener {
    public interface Listener {
        void onTranscript(String text, boolean partial);
        void onRecognizerStatus(String status);
    }

    private final Context context;
    private final Listener listener;
    private final Handler main = new Handler(Looper.getMainLooper());
    private SpeechRecognizer recognizer;
    private boolean running;
    private boolean listening;
    private long lastDeliveryAt;
    private String lastDelivery = "";

    public SpeechLoop(Context context, Listener listener) {
        this.context = context.getApplicationContext();
        this.listener = listener;
    }

    public void start() {
        main.post(() -> {
            if (running) return;
            running = true;
            createRecognizer();
            listenSoon(50);
        });
    }

    public void stop() {
        main.post(() -> {
            running = false;
            listening = false;
            if (recognizer != null) {
                recognizer.cancel();
                recognizer.destroy();
                recognizer = null;
            }
        });
    }

    private void createRecognizer() {
        if (recognizer != null) return;
        try {
            if (SpeechRecognizer.isOnDeviceRecognitionAvailable(context)) {
                recognizer = SpeechRecognizer.createOnDeviceSpeechRecognizer(context);
                listener.onRecognizerStatus("On-device speech recognition ready");
            } else {
                recognizer = SpeechRecognizer.createSpeechRecognizer(context);
                listener.onRecognizerStatus("System speech recognition ready");
            }
            recognizer.setRecognitionListener(this);
        } catch (Exception exception) {
            listener.onRecognizerStatus("Speech recognition unavailable: " + exception.getMessage());
        }
    }

    private void listenSoon(long delayMillis) {
        main.removeCallbacksAndMessages(null);
        main.postDelayed(this::beginListening, delayMillis);
    }

    private void beginListening() {
        if (!running || listening) return;
        createRecognizer();
        if (recognizer == null) {
            listenSoon(1500);
            return;
        }
        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.UK.toLanguageTag())
            .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3)
            .putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
            .putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 650L)
            .putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 400L);
        try {
            listening = true;
            recognizer.startListening(intent);
        } catch (Exception exception) {
            listening = false;
            listener.onRecognizerStatus("Recognizer restart: " + exception.getMessage());
            listenSoon(700);
        }
    }

    private void deliver(Bundle results, boolean partial) {
        ArrayList<String> values = results == null ? null : results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
        if (values == null || values.isEmpty()) return;
        String text = values.get(0) == null ? "" : values.get(0).trim();
        if (text.isEmpty()) return;
        String key = TranscriptPolicy.normalise(text) + ":" + partial;
        long now = System.currentTimeMillis();
        if (key.equals(lastDelivery) && now - lastDeliveryAt < 1200) return;
        lastDelivery = key;
        lastDeliveryAt = now;
        listener.onTranscript(text, partial);
    }

    @Override public void onReadyForSpeech(Bundle params) { listener.onRecognizerStatus("Listening"); }
    @Override public void onBeginningOfSpeech() {}
    @Override public void onRmsChanged(float rmsdB) {}
    @Override public void onBufferReceived(byte[] buffer) {}
    @Override public void onEndOfSpeech() { listening = false; }
    @Override public void onError(int error) {
        listening = false;
        if (!running) return;
        long delay = error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY ? 900 : 250;
        listenSoon(delay);
    }
    @Override public void onResults(Bundle results) {
        listening = false;
        deliver(results, false);
        if (running) listenSoon(180);
    }
    @Override public void onPartialResults(Bundle partialResults) { deliver(partialResults, true); }
    @Override public void onEvent(int eventType, Bundle params) {}
}
