package com.aaron.jarvisvoice.wear;

import android.app.PendingIntent;
import android.content.Intent;
import android.os.Bundle;
import android.os.RemoteException;
import android.speech.RecognitionService;
import android.speech.SpeechRecognizer;

/**
 * Capability required by the official assistant role. Jarvis owns recognition
 * inside its voice session, so standalone recognizer requests launch that
 * session instead of opening a second microphone pipeline.
 */
public final class WearRecognitionService extends RecognitionService {
    @Override protected void onStartListening(Intent recognizerIntent, Callback callback) {
        try {
            PendingIntent.getActivity(
                this,
                73,
                new Intent(this, JarvisWearActivity.class)
                    .putExtra(JarvisWearActivity.EXTRA_AUTO_START, true),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
            ).send();
        } catch (PendingIntent.CanceledException ignored) {}
        try {
            callback.readyForSpeech(new Bundle());
            callback.error(SpeechRecognizer.ERROR_CLIENT);
        } catch (RemoteException ignored) {}
    }

    @Override protected void onStopListening(Callback callback) {}

    @Override protected void onCancel(Callback callback) {}
}
