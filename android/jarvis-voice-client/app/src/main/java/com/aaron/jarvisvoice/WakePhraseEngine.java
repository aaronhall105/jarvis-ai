package com.aaron.jarvisvoice;

import android.Manifest;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;

import java.util.ArrayList;
import java.util.Locale;

// Dedicated local wake phrase with a separate command-verification stage.
// A wake detection never opens a Jarvis conversation by itself.
public final class WakePhraseEngine implements RecognitionListener {
    public interface Listener {
        void onWakePhrase(String transcript, String command);
        void onWakeStatus(String status);
        void onWakeError(String message);
    }

    private static final long COMMAND_TIMEOUT_MS = 5_000L;
    private static final long FALSE_WAKE_COOLDOWN_MS = 1_500L;

    private final Context context;
    private final Listener listener;
    private final Handler main = new Handler(Looper.getMainLooper());

    private SpeechRecognizer recognizer;
    private SherpaWakeWordEngine sherpaEngine;
    private boolean running;
    private boolean listening;
    private boolean triggered;
    private boolean onDevice;
    private boolean dedicated;
    private boolean dedicatedRequested;
    private boolean awaitingCommand;
    private String wakePhrase = "jarvis";
    private String detectedWakePhrase = "";
    private int restartCount;
    private int dedicatedRetryCount;
    private float dedicatedSensitivity = 0.90f;
    private long wakeCooldownUntilMs;

    private final Runnable commandTimeout =
        this::rejectUnverifiedWake;

    public WakePhraseEngine(Context context, Listener listener) {
        this.context = context.getApplicationContext();
        this.listener = listener;
    }

    public void start(String configuredWakePhrase) {
        main.post(() -> {
            stopInternal();

            if (
                context.checkSelfPermission(
                    Manifest.permission.RECORD_AUDIO
                ) != PackageManager.PERMISSION_GRANTED
            ) {
                listener.onWakeError(
                    "Microphone permission is required for the wake word"
                );
                return;
            }

            wakePhrase = WakePhrasePolicy.normalise(
                configuredWakePhrase
            );
            if (wakePhrase.isEmpty()) {
                wakePhrase = "jarvis";
            }

            running = true;
            triggered = false;
            awaitingCommand = false;
            restartCount = 0;
            dedicatedRetryCount = 0;
            wakeCooldownUntilMs = 0L;

            SecureStore store = new SecureStore(context);
            dedicatedSensitivity = store.wakeSensitivity();
            dedicatedRequested =
                store.dedicatedWakeEnabled()
                    && (
                        "jarvis".equals(wakePhrase)
                            || "hey jarvis".equals(wakePhrase)
                    );

            restartWakeDetector();
        });
    }

    public void stop() {
        main.post(this::stopInternal);
    }

    public boolean isRunning() {
        return running;
    }

    public boolean isListeningForWake() {
        return running
            && (
                listening
                    || awaitingCommand
                    || (
                        dedicated
                            && sherpaEngine != null
                    )
            );
    }

    private void restartWakeDetector() {
        if (!running || triggered || awaitingCommand) return;

        long remaining =
            wakeCooldownUntilMs - SystemClock.elapsedRealtime();
        if (remaining > 0L) {
            main.postDelayed(this::restartWakeDetector, remaining);
            return;
        }

        if (dedicatedRequested) {
            startDedicated(dedicatedSensitivity);
        } else {
            startSpeechFallback();
        }
    }

    private void startDedicated(float sensitivity) {
        if (!running || triggered || awaitingCommand) return;

        dedicatedSensitivity = sensitivity;
        dedicated = true;
        releaseRecognizer();

        sherpaEngine = new SherpaWakeWordEngine(
            context,
            new SherpaWakeWordEngine.Listener() {
                @Override public void onDetected(
                    String detectedPhrase
                ) {
                    if (
                        !running
                            || triggered
                            || awaitingCommand
                    ) {
                        return;
                    }

                    releaseDedicated();
                    dedicated = false;
                    listening = false;
                    beginCommandVerification(
                        detectedPhrase == null
                            || detectedPhrase.isBlank()
                                ? wakePhrase
                                : detectedPhrase
                    );
                }

                @Override public void onStatus(String message) {
                    if (
                        running
                            && dedicated
                            && !triggered
                            && !awaitingCommand
                    ) {
                        listening = true;
                        listener.onWakeStatus(message);
                    }
                }

                @Override public void onError(String message) {
                    if (!running || triggered) return;

                    releaseDedicated();
                    dedicated = false;
                    listening = false;
                    dedicatedRetryCount++;

                    if (dedicatedRetryCount <= 3) {
                        long delay = Math.min(
                            4_000L,
                            700L * (
                                1L << (dedicatedRetryCount - 1)
                            )
                        );
                        listener.onWakeStatus(
                            "Dedicated wake word restarting"
                        );
                        main.postDelayed(
                            this::restartDedicatedAfterError,
                            delay
                        );
                        return;
                    }

                    dedicatedRetryCount = 0;
                    dedicatedRequested = false;
                    listener.onWakeStatus(
                        "Dedicated wake word unavailable — "
                            + "using Android fallback"
                    );
                    startSpeechFallback();
                }

                private void restartDedicatedAfterError() {
                    if (
                        running
                            && !triggered
                            && !awaitingCommand
                    ) {
                        startDedicated(dedicatedSensitivity);
                    }
                }
            }
        );

        sherpaEngine.start(sensitivity, wakePhrase);
    }

    private void startSpeechFallback() {
        if (!running || triggered || awaitingCommand) return;
        dedicated = false;
        createRecognizer();
        listenSoon(160L);
    }

    private void beginCommandVerification(
        String detectedPhrase
    ) {
        if (!running || triggered) return;

        awaitingCommand = true;
        detectedWakePhrase = WakePhrasePolicy.normalise(
            detectedPhrase
        );
        if (detectedWakePhrase.isEmpty()) {
            detectedWakePhrase = wakePhrase;
        }

        dedicated = false;
        listening = false;
        releaseDedicated();
        releaseRecognizer();
        createRecognizer();

        listener.onWakeStatus(
            "Wake phrase heard — listening for a command"
        );

        main.removeCallbacks(commandTimeout);
        main.postDelayed(commandTimeout, COMMAND_TIMEOUT_MS);
        listenSoon(80L);
    }

    private void rejectUnverifiedWake() {
        if (!running || triggered || !awaitingCommand) return;

        awaitingCommand = false;
        detectedWakePhrase = "";
        listening = false;
        main.removeCallbacks(commandTimeout);
        releaseRecognizer();

        wakeCooldownUntilMs =
            SystemClock.elapsedRealtime()
                + FALSE_WAKE_COOLDOWN_MS;

        listener.onWakeStatus(
            "False wake ignored — no command was heard"
        );

        main.postDelayed(
            this::restartWakeDetector,
            FALSE_WAKE_COOLDOWN_MS
        );
    }

    private void deliverVerifiedCommand(
        String transcript,
        String command
    ) {
        String verified = WakeCommandPolicy.commandAfterWake(
            command,
            wakePhrase
        );
        if (verified.isEmpty()) {
            rejectUnverifiedWake();
            return;
        }

        triggered = true;
        awaitingCommand = false;
        running = false;
        listening = false;
        main.removeCallbacksAndMessages(null);
        releaseDedicated();
        releaseRecognizer();

        listener.onWakePhrase(
            transcript == null ? detectedWakePhrase : transcript,
            verified
        );
    }

    private void stopInternal() {
        running = false;
        listening = false;
        triggered = false;
        dedicated = false;
        dedicatedRequested = false;
        awaitingCommand = false;
        detectedWakePhrase = "";
        restartCount = 0;
        dedicatedRetryCount = 0;
        wakeCooldownUntilMs = 0L;
        main.removeCallbacksAndMessages(null);
        releaseDedicated();
        releaseRecognizer();
    }

    private void releaseDedicated() {
        SherpaWakeWordEngine current = sherpaEngine;
        sherpaEngine = null;
        if (current != null) current.stop();
    }

    private void releaseRecognizer() {
        SpeechRecognizer current = recognizer;
        recognizer = null;
        if (current != null) {
            try {
                current.cancel();
            } catch (Exception ignored) {
            }
            try {
                current.destroy();
            } catch (Exception ignored) {
            }
        }
    }

    private void recreateRecognizer() {
        listening = false;
        releaseRecognizer();
        createRecognizer();
    }

    private void createRecognizer() {
        if (
            recognizer != null
                || !running
                || dedicated
                || triggered
        ) {
            return;
        }

        try {
            onDevice =
                SpeechRecognizer.isOnDeviceRecognitionAvailable(
                    context
                );

            if (onDevice) {
                recognizer =
                    SpeechRecognizer
                        .createOnDeviceSpeechRecognizer(context);
            } else if (
                SpeechRecognizer.isRecognitionAvailable(context)
            ) {
                recognizer =
                    SpeechRecognizer.createSpeechRecognizer(
                        context
                    );
            } else {
                running = false;
                listener.onWakeError(
                    "No Android speech recogniser is available"
                );
                return;
            }

            recognizer.setRecognitionListener(this);

            if (!awaitingCommand) {
                listener.onWakeStatus(
                    onDevice
                        ? "Android wake fallback ready on device — say \""
                            + wakePhrase + "\""
                        : "Android wake fallback ready — say \""
                            + wakePhrase + "\""
                );
            }
        } catch (Exception firstFailure) {
            releaseRecognizer();

            if (
                onDevice
                    && SpeechRecognizer
                        .isRecognitionAvailable(context)
            ) {
                try {
                    onDevice = false;
                    recognizer =
                        SpeechRecognizer.createSpeechRecognizer(
                            context
                        );
                    recognizer.setRecognitionListener(this);
                    return;
                } catch (Exception ignored) {
                    releaseRecognizer();
                }
            }

            running = false;
            listener.onWakeError(
                "Wake recogniser unavailable: "
                    + safeMessage(firstFailure)
            );
        }
    }

    private void listenSoon(long delayMillis) {
        if (
            !running
                || triggered
                || dedicated
                || listening
        ) {
            return;
        }
        main.postDelayed(this::beginListening, delayMillis);
    }

    private void beginListening() {
        if (
            !running
                || listening
                || triggered
                || dedicated
        ) {
            return;
        }

        if (
            context.checkSelfPermission(
                Manifest.permission.RECORD_AUDIO
            ) != PackageManager.PERMISSION_GRANTED
        ) {
            stopInternal();
            listener.onWakeError(
                "Microphone permission was removed"
            );
            return;
        }

        createRecognizer();
        if (recognizer == null) {
            listenSoon(2_000L);
            return;
        }

        Intent intent =
            new Intent(
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
                    RecognizerIntent.EXTRA_PARTIAL_RESULTS,
                    false
                )
                .putExtra(
                    RecognizerIntent.EXTRA_MAX_RESULTS,
                    5
                )
                .putExtra(
                    RecognizerIntent.EXTRA_PREFER_OFFLINE,
                    onDevice
                )
                .putExtra(
                    RecognizerIntent
                        .EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS,
                    awaitingCommand ? 700L : 850L
                )
                .putExtra(
                    RecognizerIntent
                        .EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS,
                    awaitingCommand ? 400L : 500L
                );

        try {
            listening = true;
            recognizer.startListening(intent);
        } catch (Exception exception) {
            listening = false;
            restartCount++;
            recreateRecognizer();

            if (awaitingCommand) {
                rejectUnverifiedWake();
            } else {
                listener.onWakeStatus(
                    "Android wake fallback restarting"
                );
                listenSoon(backoff(700L));
            }
        }
    }

    private void evaluateWakeResults(Bundle results) {
        ArrayList<String> values =
            recognitionValues(results);
        if (
            values == null
                || values.isEmpty()
                || triggered
        ) {
            return;
        }

        for (String value : values) {
            WakePhrasePolicy.Decision decision =
                WakePhrasePolicy.evaluate(
                    value,
                    wakePhrase
                );
            if (!decision.triggered) continue;

            String command =
                WakeCommandPolicy.commandAfterWake(
                    decision.command,
                    wakePhrase
                );

            if (!command.isEmpty()) {
                deliverVerifiedCommand(value, command);
            } else {
                beginCommandVerification(
                    decision.matchedPhrase
                );
            }
            return;
        }
    }

    private void evaluateCommandResults(Bundle results) {
        ArrayList<String> values =
            recognitionValues(results);
        if (values != null) {
            for (String value : values) {
                String command =
                    WakeCommandPolicy.commandAfterWake(
                        value,
                        wakePhrase
                    );
                if (!command.isEmpty()) {
                    deliverVerifiedCommand(
                        detectedWakePhrase,
                        command
                    );
                    return;
                }
            }
        }

        rejectUnverifiedWake();
    }

    private static ArrayList<String> recognitionValues(
        Bundle results
    ) {
        return results == null
            ? null
            : results.getStringArrayList(
                SpeechRecognizer.RESULTS_RECOGNITION
            );
    }

    private long backoff(long base) {
        int multiplier =
            Math.min(6, Math.max(1, restartCount));
        return Math.min(6_000L, base * multiplier);
    }

    @Override public void onReadyForSpeech(Bundle params) {
        restartCount = 0;
        listener.onWakeStatus(
            awaitingCommand
                ? "Listening for your command"
                : "Listening for \"" + wakePhrase + "\""
        );
    }

    @Override public void onBeginningOfSpeech() {
    }

    @Override public void onRmsChanged(float rmsdB) {
    }

    @Override public void onBufferReceived(byte[] buffer) {
    }

    @Override public void onEndOfSpeech() {
        listening = false;
    }

    @Override public void onError(int error) {
        listening = false;
        if (!running || triggered || dedicated) return;

        if (
            error
                == SpeechRecognizer
                    .ERROR_INSUFFICIENT_PERMISSIONS
        ) {
            stopInternal();
            listener.onWakeError(
                "Microphone permission is required for the wake word"
            );
            return;
        }

        if (awaitingCommand) {
            rejectUnverifiedWake();
            return;
        }

        boolean recreate =
            error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY
                || error == SpeechRecognizer.ERROR_CLIENT
                || error
                    == SpeechRecognizer
                        .ERROR_SERVER_DISCONNECTED
                || error
                    == SpeechRecognizer
                        .ERROR_LANGUAGE_UNAVAILABLE
                || error
                    == SpeechRecognizer
                        .ERROR_LANGUAGE_NOT_SUPPORTED;

        restartCount++;
        if (recreate) recreateRecognizer();

        long baseDelay = switch (error) {
            case SpeechRecognizer.ERROR_NO_MATCH,
                 SpeechRecognizer.ERROR_SPEECH_TIMEOUT ->
                220L;
            case SpeechRecognizer.ERROR_RECOGNIZER_BUSY ->
                1_000L;
            case SpeechRecognizer.ERROR_TOO_MANY_REQUESTS ->
                4_000L;
            case SpeechRecognizer.ERROR_SERVER_DISCONNECTED ->
                2_000L;
            default -> 500L;
        };

        listenSoon(backoff(baseDelay));
    }

    @Override public void onResults(Bundle results) {
        listening = false;

        if (awaitingCommand) {
            evaluateCommandResults(results);
            return;
        }

        evaluateWakeResults(results);

        if (
            running
                && !triggered
                && !awaitingCommand
                && !dedicated
        ) {
            restartCount = 0;
            listenSoon(200L);
        }
    }

    @Override public void onPartialResults(
        Bundle partialResults
    ) {
        // Deliberately ignored. A final result is required before
        // a wake or follow-up command can open a conversation.
    }

    @Override public void onEvent(
        int eventType,
        Bundle params
    ) {
    }

    private static String safeMessage(
        Exception exception
    ) {
        String value = exception.getMessage();
        return value == null || value.isBlank()
            ? exception.getClass().getSimpleName()
            : value;
    }
}
