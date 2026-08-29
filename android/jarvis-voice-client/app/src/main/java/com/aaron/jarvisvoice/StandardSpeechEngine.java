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

public final class StandardSpeechEngine {

    public interface Listener {
        void onStandardReady();

        default void onStandardSpeechStarted() {}

        void onStandardPartial(String text);
        void onStandardFinal(String text);
        void onStandardError(String message);
    }

    private final Context context;
    private final Listener listener;
    private final Handler main =
        new Handler(Looper.getMainLooper());

    private SpeechRecognizer recognizer;

    private boolean running;
    private boolean captureReady;
    private boolean onDeviceFallback;

    private float lastConfidence = -1.0f;

    /*
     * Every recogniser instance owns exactly one generation.
     * Delayed callbacks from a cancelled/destroyed recogniser
     * cannot mutate its replacement.
     */
    private int recognitionGeneration;

    public StandardSpeechEngine(
        Context context,
        Listener listener
    ) {
        this.context =
            context.getApplicationContext();
        this.listener = listener;
    }

    public void start() {
        main.post(() -> {
            /*
             * Invalidate the previous recogniser before creating
             * the replacement.
             */
            stopInternal();

            int generation =
                recognitionGeneration;

            running = true;
            captureReady = false;

            try {
                createRecognizer(generation);
                recognizer.startListening(intent());
            } catch (Exception failure) {
                if (
                    isCurrent(generation)
                        && !onDeviceFallback
                        && SpeechRecognizer
                            .isOnDeviceRecognitionAvailable(
                                context
                            )
                ) {
                    if (
                        startOnDeviceFallback(
                            generation
                        )
                    ) {
                        return;
                    }
                }

                if (!isCurrent(generation)) {
                    return;
                }

                stopInternal();

                listener.onStandardError(
                    "Speech recognition could not start: "
                        + safeMessage(failure)
                );
            }
        });
    }

    public void stop() {
        main.post(this::stopInternal);
    }

    public boolean isRunning() {
        return running;
    }

    public boolean isCaptureReady() {
        return captureReady;
    }

    public float lastConfidence() {
        return lastConfidence;
    }

    private boolean isCurrent(
        int generation
    ) {
        return CaptureEpochPolicy.mayPublish(
            running,
            generation,
            recognitionGeneration
        );
    }

    private void createRecognizer(
        int generation
    ) {
        onDeviceFallback = false;

        if (
            SpeechRecognizer
                .isRecognitionAvailable(context)
        ) {
            recognizer =
                SpeechRecognizer
                    .createSpeechRecognizer(
                        context
                    );
        } else if (
            SpeechRecognizer
                .isOnDeviceRecognitionAvailable(
                    context
                )
        ) {
            onDeviceFallback = true;
            recognizer =
                SpeechRecognizer
                    .createOnDeviceSpeechRecognizer(
                        context
                    );
        } else {
            throw new IllegalStateException(
                "No Android speech recogniser is available"
            );
        }

        recognizer.setRecognitionListener(
            new GuardedRecognitionListener(
                generation
            )
        );
    }

    private boolean startOnDeviceFallback(
        int failedGeneration
    ) {
        if (!isCurrent(failedGeneration)) {
            return false;
        }

        /*
         * The fallback is a new recogniser and therefore gets
         * its own generation. Any callback from the failed
         * primary becomes stale immediately.
         */
        releaseRecognizer();

        int generation =
            ++recognitionGeneration;

        running = true;
        captureReady = false;
        onDeviceFallback = true;

        try {
            recognizer =
                SpeechRecognizer
                    .createOnDeviceSpeechRecognizer(
                        context
                    );

            recognizer.setRecognitionListener(
                new GuardedRecognitionListener(
                    generation
                )
            );

            recognizer.startListening(intent());
            return true;
        } catch (Exception failure) {
            /*
             * The fallback generation now owns the recogniser.
             * If startup fails, clean up that exact generation
             * here rather than returning control to the stale
             * primary generation.
             */
            if (isCurrent(generation)) {
                stopInternal();

                listener.onStandardError(
                    "Speech recognition could not start: "
                        + safeMessage(failure)
                );
            }

            /*
             * The fallback attempt has been fully handled,
             * successful or not. The caller must not resume
             * cleanup using the obsolete primary generation.
             */
            return true;
        }
    }

    private Intent intent() {
        return new Intent(
            RecognizerIntent.ACTION_RECOGNIZE_SPEECH
        )
            .putExtra(
                RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                RecognizerIntent.LANGUAGE_MODEL_FREE_FORM
            )
            .putExtra(
                RecognizerIntent.EXTRA_LANGUAGE,
                Locale.UK.toLanguageTag()
            )
            .putExtra(
                RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE,
                Locale.UK.toLanguageTag()
            )
            .putExtra(
                RecognizerIntent.EXTRA_PARTIAL_RESULTS,
                true
            )
            .putExtra(
                RecognizerIntent.EXTRA_MAX_RESULTS,
                5
            )
            .putExtra(
                RecognizerIntent.EXTRA_PREFER_OFFLINE,
                onDeviceFallback
            )
            .putExtra(
                RecognizerIntent
                    .EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS,
                900L
            )
            .putExtra(
                RecognizerIntent
                    .EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS,
                600L
            )
            .putExtra(
                RecognizerIntent
                    .EXTRA_SPEECH_INPUT_MINIMUM_LENGTH_MILLIS,
                350L
            );
    }

    private void stopInternal() {
        /*
         * Increment first. Any callback already queued by Android
         * immediately loses ownership.
         */
        ++recognitionGeneration;

        running = false;
        captureReady = false;

        releaseRecognizer();
    }

    private void releaseRecognizer() {
        SpeechRecognizer current =
            recognizer;

        recognizer = null;

        if (current != null) {
            try {
                current.cancel();
            } catch (Exception ignored) {}

            try {
                current.destroy();
            } catch (Exception ignored) {}
        }
    }

    private static final class RecognitionResult {
        final String text;
        final float confidence;

        RecognitionResult(
            String text,
            float confidence
        ) {
            this.text = text;
            this.confidence = confidence;
        }
    }

    private static RecognitionResult best(
        Bundle bundle
    ) {
        ArrayList<String> results =
            bundle == null
                ? null
                : bundle.getStringArrayList(
                    SpeechRecognizer
                        .RESULTS_RECOGNITION
                );

        if (
            results == null
                || results.isEmpty()
        ) {
            return new RecognitionResult(
                "",
                -1.0f
            );
        }

        float[] confidence =
            bundle.getFloatArray(
                SpeechRecognizer
                    .CONFIDENCE_SCORES
            );

        int bestIndex = 0;
        float bestScore = -1.0f;

        if (confidence != null) {
            int maximum =
                Math.min(
                    confidence.length,
                    results.size()
                );

            for (
                int index = 0;
                index < maximum;
                index++
            ) {
                if (
                    confidence[index]
                        > bestScore
                ) {
                    bestScore =
                        confidence[index];
                    bestIndex = index;
                }
            }
        }

        String value =
            results.get(bestIndex);

        return new RecognitionResult(
            value == null
                ? ""
                : value.trim(),
            bestScore
        );
    }

    private void handleReady(
        int generation
    ) {
        if (!isCurrent(generation)) {
            return;
        }

        captureReady = true;
        listener.onStandardReady();
    }

    private void handleSpeechStarted(
        int generation
    ) {
        if (!isCurrent(generation)) {
            return;
        }

        captureReady = true;
        listener.onStandardSpeechStarted();
    }

    private void handleEndOfSpeech(
        int generation
    ) {
        if (!isCurrent(generation)) {
            return;
        }

        /*
         * The microphone has stopped accepting new speech while
         * Android finalises recognition.
         */
        captureReady = false;
    }

    private void handleError(
        int generation,
        int error
    ) {
        if (!isCurrent(generation)) {
            return;
        }

        stopInternal();

        String message = switch (error) {
            case SpeechRecognizer.ERROR_NO_MATCH ->
                "I did not catch that.";

            case SpeechRecognizer.ERROR_SPEECH_TIMEOUT ->
                "I did not hear anything.";

            case SpeechRecognizer.ERROR_NETWORK,
                 SpeechRecognizer.ERROR_NETWORK_TIMEOUT ->
                "Speech recognition network error.";

            case SpeechRecognizer.ERROR_RECOGNIZER_BUSY ->
                "Speech recogniser is busy.";

            case SpeechRecognizer.ERROR_TOO_MANY_REQUESTS ->
                "Speech recognition is temporarily rate limited.";

            default ->
                "Speech recognition stopped ("
                    + error
                    + ").";
        };

        listener.onStandardError(message);
    }

    private void handleResults(
        int generation,
        Bundle results
    ) {
        if (!isCurrent(generation)) {
            return;
        }

        RecognitionResult result =
            best(results);

        lastConfidence =
            result.confidence;

        stopInternal();

        if (result.text.isEmpty()) {
            listener.onStandardError(
                "I did not catch that."
            );
        } else {
            listener.onStandardFinal(
                result.text
            );
        }
    }

    private void handlePartial(
        int generation,
        Bundle partialResults
    ) {
        if (!isCurrent(generation)) {
            return;
        }

        RecognitionResult result =
            best(partialResults);

        lastConfidence =
            result.confidence;

        if (!result.text.isEmpty()) {
            listener.onStandardPartial(
                result.text
            );
        }
    }

    private final class GuardedRecognitionListener
        implements RecognitionListener {

        private final int generation;

        GuardedRecognitionListener(
            int generation
        ) {
            this.generation =
                generation;
        }

        @Override
        public void onReadyForSpeech(
            Bundle params
        ) {
            handleReady(generation);
        }

        @Override
        public void onBeginningOfSpeech() {
            handleSpeechStarted(
                generation
            );
        }

        @Override
        public void onRmsChanged(
            float rmsdB
        ) {}

        @Override
        public void onBufferReceived(
            byte[] buffer
        ) {}

        @Override
        public void onEndOfSpeech() {
            handleEndOfSpeech(
                generation
            );
        }

        @Override
        public void onError(
            int error
        ) {
            handleError(
                generation,
                error
            );
        }

        @Override
        public void onResults(
            Bundle results
        ) {
            handleResults(
                generation,
                results
            );
        }

        @Override
        public void onPartialResults(
            Bundle partialResults
        ) {
            handlePartial(
                generation,
                partialResults
            );
        }

        @Override
        public void onEvent(
            int eventType,
            Bundle params
        ) {}
    }

    private static String safeMessage(
        Exception exception
    ) {
        String value =
            exception.getMessage();

        return value == null
            || value.isBlank()
                ? exception
                    .getClass()
                    .getSimpleName()
                : value;
    }
}
