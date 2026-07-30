package com.aaron.jarvisvoice;

import android.Manifest;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.service.voice.VoiceInteractionService;

import java.lang.ref.WeakReference;

/** System-selected Android assistant and authoritative wake-word host. */
public final class JarvisVoiceInteractionService
    extends VoiceInteractionService
    implements WakePhraseEngine.Listener {

    public static final String ARG_COMMAND = "jarvis_command";
    public static final String ARG_SOURCE = "jarvis_source";

    private static final long HEALTH_INTERVAL_MS = 15_000L;

    private static WeakReference<JarvisVoiceInteractionService> active =
        new WeakReference<>(null);

    private final Handler main = new Handler(Looper.getMainLooper());
    private final Runnable healthCheck = this::checkWakeHealth;

    private SecureStore store;
    private WakePhraseEngine wakePhraseEngine;
    private boolean systemReady;
    private String armedPhrase = "";
    private int retryAttempt;

    @Override public void onCreate() {
        super.onCreate();
        store = new SecureStore(this);
        wakePhraseEngine = new WakePhraseEngine(this, this);
    }

    @Override public void onReady() {
        super.onReady();
        systemReady = true;
        active = new WeakReference<>(this);
        warmJarvisCore();
        ensureWakePhrase();
    }

    @Override public void onShutdown() {
        systemReady = false;
        stopWakePhrase();
        JarvisVoiceInteractionService current = active.get();
        if (current == this) active = new WeakReference<>(null);
        try {
            startService(
                new Intent(this, VoiceService.class)
                    .setAction(VoiceService.ACTION_STOP)
            );
        } catch (Exception ignored) {}
        super.onShutdown();
    }

    @Override public void onDestroy() {
        systemReady = false;
        stopWakePhrase();
        JarvisVoiceInteractionService current = active.get();
        if (current == this) active = new WeakReference<>(null);
        super.onDestroy();
    }

    private void warmJarvisCore() {
        if (!store.hasMobileToken() || store.coreUrl().isBlank()) {
            return;
        }
        try {
            startForegroundService(
                new Intent(this, VoiceService.class)
                    .setAction(VoiceService.ACTION_START)
            );
        } catch (Exception ignored) {}
    }

    private boolean wakeConfigurationReady() {
        return systemReady
            && store.assistantWakeAlways()
            && store.wakeEnabled()
            && checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED
            && store.hasMobileToken()
            && !store.coreUrl().isBlank()
            && !VoiceSessionState.isActive();
    }

    private void refreshWakePhrase() {
        stopWakePhrase();
        store = new SecureStore(this);
        retryAttempt = 0;
        armWakePhrase();
    }

    private void ensureWakePhrase() {
        store = new SecureStore(this);

        if (!systemReady) return;

        if (VoiceSessionState.isActive()) {
            stopWakeEngineOnly();
            scheduleHealthCheck();
            return;
        }

        if (!store.assistantWakeAlways() || !store.wakeEnabled()) {
            stopWakePhrase();
            broadcastWakeStatus("Wake word is off", false);
            return;
        }

        if (
            checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED
        ) {
            stopWakePhrase();
            broadcastWakeError(
                "Open Jarvis and allow microphone access"
            );
            return;
        }

        if (!store.hasMobileToken() || store.coreUrl().isBlank()) {
            stopWakePhrase();
            broadcastWakeError(
                "Open Jarvis Settings and connect Jarvis Core"
            );
            return;
        }

        String desired = store.wakePhrase();
        if (
            wakePhraseEngine.isRunning()
                && desired.equals(armedPhrase)
        ) {
            scheduleHealthCheck();
            return;
        }

        stopWakeEngineOnly();
        armWakePhrase();
    }

    private void armWakePhrase() {
        if (!wakeConfigurationReady()) return;
        armedPhrase = store.wakePhrase();
        wakePhraseEngine.start(armedPhrase);
        scheduleHealthCheck();
    }

    private void checkWakeHealth() {
        if (!systemReady) return;
        store = new SecureStore(this);

        if (VoiceSessionState.isActive()) {
            stopWakeEngineOnly();
            scheduleHealthCheck();
            return;
        }

        if (!wakeConfigurationReady()) {
            ensureWakePhrase();
            return;
        }

        if (!wakePhraseEngine.isRunning()) {
            armedPhrase = "";
            ensureWakePhrase();
            return;
        }

        scheduleHealthCheck();
    }

    private void scheduleHealthCheck() {
        main.removeCallbacks(healthCheck);
        if (systemReady) {
            main.postDelayed(healthCheck, HEALTH_INTERVAL_MS);
        }
    }

    private void stopWakeEngineOnly() {
        main.removeCallbacks(healthCheck);
        if (wakePhraseEngine != null) wakePhraseEngine.stop();
        armedPhrase = "";
    }

    private void stopWakePhrase() {
        main.removeCallbacksAndMessages(null);
        stopWakeEngineOnly();
        retryAttempt = 0;
    }

    @Override public void onWakePhrase(
        String transcript,
        String command
    ) {
        stopWakePhrase();

        if (MainActivity.isVisible()) {
            startVoiceInExistingApp(command);
            return;
        }

        if (showOverlay(command, "wake_word")) return;
        openFullAssistant(command);
    }

    @Override public void onWakeStatus(String message) {
        retryAttempt = 0;
        broadcastWakeStatus(message, true);
        scheduleHealthCheck();
    }

    @Override public void onWakeError(String message) {
        broadcastWakeError(message);
        stopWakeEngineOnly();

        if (
            !systemReady
                || !store.assistantWakeAlways()
                || !store.wakeEnabled()
        ) {
            return;
        }

        int attempt = Math.min(5, retryAttempt++);
        long delay = Math.min(
            12_000L,
            800L * (1L << attempt)
        );
        main.postDelayed(this::ensureWakePhrase, delay);
    }

    private void broadcastWakeStatus(
        String message,
        boolean listening
    ) {
        Intent update = new Intent(VoiceService.ACTION_EVENT)
            .setPackage(getPackageName())
            .putExtra(VoiceService.EXTRA_EVENT, "status")
            .putExtra(VoiceService.EXTRA_TEXT, message)
            .putExtra(VoiceService.EXTRA_ACTIVE, false)
            .putExtra(
                VoiceService.EXTRA_LISTENING,
                listening
            );
        sendBroadcast(update);
    }

    private void broadcastWakeError(String message) {
        Intent update = new Intent(VoiceService.ACTION_EVENT)
            .setPackage(getPackageName())
            .putExtra(VoiceService.EXTRA_EVENT, "error")
            .putExtra(VoiceService.EXTRA_TEXT, message)
            .putExtra(VoiceService.EXTRA_ACTIVE, false)
            .putExtra(VoiceService.EXTRA_LISTENING, false);
        sendBroadcast(update);
    }

    private boolean showOverlay(
        String command,
        String source
    ) {
        if (MainActivity.isVisible()) return false;
        if (!systemReady || !store.assistantOverlayEnabled()) {
            return false;
        }

        VoiceSessionState.setActive(true);

        Bundle args = new Bundle();
        args.putString(
            ARG_COMMAND,
            command == null ? "" : command.trim()
        );
        args.putString(
            ARG_SOURCE,
            source == null ? "assistant" : source
        );
        showSession(args, 0);
        return true;
    }

    private void startVoiceInExistingApp(String command) {
        VoiceSessionState.setActive(true);
        Intent voice = new Intent(this, VoiceService.class);
        if (command != null && !command.isBlank()) {
            voice.setAction(VoiceService.ACTION_ASSISTANT_INVOKE)
                .putExtra(
                    VoiceService.EXTRA_TEXT,
                    command.trim()
                );
        } else {
            voice.setAction(VoiceService.ACTION_START_VOICE);
        }

        try {
            startForegroundService(voice);
        } catch (Exception exception) {
            VoiceSessionState.setActive(false);
            main.postDelayed(this::ensureWakePhrase, 1_000L);
        }
    }

    private void openFullAssistant(String command) {
        Intent open = new Intent(this, MainActivity.class)
            .addFlags(
                Intent.FLAG_ACTIVITY_NEW_TASK
                    | Intent.FLAG_ACTIVITY_CLEAR_TOP
                    | Intent.FLAG_ACTIVITY_SINGLE_TOP
            );
        startActivity(open);
        startVoiceInExistingApp(command);
    }

    public static boolean showOverlayIfActive(
        Context context,
        String command,
        String source
    ) {
        if (MainActivity.isVisible()) return false;

        JarvisVoiceInteractionService service = active.get();
        if (
            service == null
                || !isActiveAssistant(context)
                || !service.systemReady
                || !service.store.assistantOverlayEnabled()
        ) {
            return false;
        }

        service.main.post(
            () -> service.showOverlay(command, source)
        );
        return true;
    }

    public static void refreshWakeIfActive(Context context) {
        JarvisVoiceInteractionService service = active.get();
        if (
            service == null
                || !isActiveAssistant(context)
        ) {
            return;
        }
        service.main.post(service::refreshWakePhrase);
    }

    public static void ensureWakeIfActive(Context context) {
        JarvisVoiceInteractionService service = active.get();
        if (
            service == null
                || !isActiveAssistant(context)
        ) {
            return;
        }
        service.main.post(service::ensureWakePhrase);
    }

    public static void rearmWakeIfActive(Context context) {
        JarvisVoiceInteractionService service = active.get();
        if (
            service == null
                || !isActiveAssistant(context)
        ) {
            return;
        }
        service.main.postDelayed(
            service::ensureWakePhrase,
            650L
        );
    }

    public static boolean isActiveAssistant(Context context) {
        ComponentName component = new ComponentName(
            context,
            JarvisVoiceInteractionService.class
        );
        return VoiceInteractionService.isActiveService(
            context,
            component
        );
    }
}
