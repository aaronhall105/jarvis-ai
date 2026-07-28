package com.aaron.jarvisvoice;

import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.RemoteException;
import android.speech.RecognitionListener;
import android.speech.RecognitionService;
import android.speech.SpeechRecognizer;

/**
 * RecognitionService required by the Android assistant role.
 * It delegates to Android's explicit on-device recogniser, avoiding a loop through
 * the currently selected default recognition service.
 */
public final class JarvisRecognitionService extends RecognitionService {
    private final Handler main = new Handler(Looper.getMainLooper());
    private SpeechRecognizer recognizer;
    private Callback callback;

    @Override protected void onStartListening(Intent recognizerIntent, Callback listener) {
        main.post(() -> startOnDevice(recognizerIntent, listener));
    }

    @Override protected void onCancel(Callback listener) {
        main.post(() -> {
            if (recognizer != null) {
                try { recognizer.cancel(); } catch (Exception ignored) {}
            }
            releaseRecognizer();
        });
    }

    @Override protected void onStopListening(Callback listener) {
        main.post(() -> {
            if (recognizer != null) {
                try { recognizer.stopListening(); } catch (Exception ignored) {}
            }
        });
    }

    private void startOnDevice(Intent intent, Callback listener) {
        releaseRecognizer();
        callback = listener;
        try {
            if (!SpeechRecognizer.isOnDeviceRecognitionAvailable(this)) {
                listener.error(SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE);
                callback = null;
                return;
            }
            recognizer = SpeechRecognizer.createOnDeviceSpeechRecognizer(this);
            recognizer.setRecognitionListener(new Relay());
            recognizer.startListening(intent);
        } catch (Exception exception) {
            try { listener.error(SpeechRecognizer.ERROR_CLIENT); } catch (RemoteException ignored) {}
            releaseRecognizer();
        }
    }

    private void releaseRecognizer() {
        SpeechRecognizer current = recognizer;
        recognizer = null;
        callback = null;
        if (current != null) {
            try { current.destroy(); } catch (Exception ignored) {}
        }
    }

    @Override public void onDestroy() {
        releaseRecognizer();
        super.onDestroy();
    }

    private final class Relay implements RecognitionListener {
        private Callback current() { return callback; }

        @Override public void onReadyForSpeech(Bundle params) {
            Callback value = current();
            if (value == null) return;
            try { value.readyForSpeech(params == null ? Bundle.EMPTY : params); } catch (RemoteException ignored) {}
        }

        @Override public void onBeginningOfSpeech() {
            Callback value = current();
            if (value == null) return;
            try { value.beginningOfSpeech(); } catch (RemoteException ignored) {}
        }

        @Override public void onRmsChanged(float rmsdB) {
            Callback value = current();
            if (value == null) return;
            try { value.rmsChanged(rmsdB); } catch (RemoteException ignored) {}
        }

        @Override public void onBufferReceived(byte[] buffer) {
            Callback value = current();
            if (value == null) return;
            try { value.bufferReceived(buffer); } catch (RemoteException ignored) {}
        }

        @Override public void onEndOfSpeech() {
            Callback value = current();
            if (value == null) return;
            try { value.endOfSpeech(); } catch (RemoteException ignored) {}
        }

        @Override public void onError(int error) {
            Callback value = current();
            if (value != null) {
                try { value.error(error); } catch (RemoteException ignored) {}
            }
            releaseRecognizer();
        }

        @Override public void onResults(Bundle results) {
            Callback value = current();
            if (value != null) {
                try { value.results(results == null ? Bundle.EMPTY : results); } catch (RemoteException ignored) {}
            }
            releaseRecognizer();
        }

        @Override public void onPartialResults(Bundle partialResults) {
            Callback value = current();
            if (value == null) return;
            try { value.partialResults(partialResults == null ? Bundle.EMPTY : partialResults); } catch (RemoteException ignored) {}
        }

        @Override public void onEvent(int eventType, Bundle params) { }
    }
}
