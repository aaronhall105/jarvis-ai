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

/** System-selected Android assistant and wake-phrase host. */
public final class JarvisVoiceInteractionService extends VoiceInteractionService
    implements WakePhraseEngine.Listener {

    public static final String ARG_COMMAND = "jarvis_command";
    public static final String ARG_SOURCE = "jarvis_source";

    private static WeakReference<JarvisVoiceInteractionService> active =
        new WeakReference<>(null);

    private final Handler main = new Handler(Looper.getMainLooper());
    private SecureStore store;
    private WakePhraseEngine wakePhraseEngine;
    private boolean systemReady;

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
        refreshWakePhrase();
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
        if (!store.hasMobileToken() || store.coreUrl().isBlank()) return;
        try {
            startForegroundService(
                new Intent(this, VoiceService.class)
                    .setAction(VoiceService.ACTION_START)
            );
        } catch (Exception ignored) {}
    }

    private void refreshWakePhrase() {
        stopWakePhrase();
        store = new SecureStore(this);
        armWakePhrase();
    }

    private void armWakePhrase() {
        main.post(() -> {
            if (!systemReady) return;
            if (!store.assistantWakeAlways() || !store.wakeEnabled()) {
                broadcastWakeStatus("Wake word is off", false);
                return;
            }
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                    != PackageManager.PERMISSION_GRANTED) {
                broadcastWakeError("Open Jarvis and allow microphone access");
                return;
            }
            if (!store.hasMobileToken() || store.coreUrl().isBlank()) {
                broadcastWakeError("Open Jarvis Settings and connect Jarvis Core");
                return;
            }
            wakePhraseEngine.start(store.wakePhrase());
        });
    }

    private void stopWakePhrase() {
        main.removeCallbacksAndMessages(null);
        if (wakePhraseEngine != null) wakePhraseEngine.stop();
    }

    @Override public void onWakePhrase(String transcript, String command) {
        if (showOverlay(command, "wake_word")) return;
        openFullAssistant(command);
    }

    @Override public void onWakeStatus(String message) {
        broadcastWakeStatus(message, true);
    }

    @Override public void onWakeError(String message) {
        broadcastWakeError(message);
        if (systemReady && store.assistantWakeAlways() && store.wakeEnabled()) {
            main.postDelayed(this::refreshWakePhrase, 1_500L);
        }
    }

    private void broadcastWakeStatus(String message, boolean listening) {
        Intent update = new Intent(VoiceService.ACTION_EVENT)
            .setPackage(getPackageName())
            .putExtra(VoiceService.EXTRA_EVENT, "status")
            .putExtra(VoiceService.EXTRA_TEXT, message)
            .putExtra(VoiceService.EXTRA_ACTIVE, false)
            .putExtra(VoiceService.EXTRA_LISTENING, listening);
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

    private boolean showOverlay(String command, String source) {
        if (MainActivity.isForeground()) return false;
        if (!systemReady || !store.assistantOverlayEnabled()) return false;
        stopWakePhrase();
        Bundle args = new Bundle();
        args.putString(ARG_COMMAND, command == null ? "" : command.trim());
        args.putString(ARG_SOURCE, source == null ? "assistant" : source);
        showSession(args, 0);
        return true;
    }

    private void openFullAssistant(String command) {
        stopWakePhrase();
        Intent open = new Intent(this, MainActivity.class)
            .addFlags(
                Intent.FLAG_ACTIVITY_NEW_TASK
                    | Intent.FLAG_ACTIVITY_CLEAR_TOP
                    | Intent.FLAG_ACTIVITY_SINGLE_TOP
            );
        startActivity(open);

        Intent voice = new Intent(this, VoiceService.class)
            .setAction(VoiceService.ACTION_START_VOICE);
        if (command != null && !command.isBlank()) {
            voice.setAction(VoiceService.ACTION_ASSISTANT_INVOKE)
                .putExtra(VoiceService.EXTRA_TEXT, command.trim());
        }
        try {
            startForegroundService(voice);
        } catch (Exception exception) {
            main.postDelayed(this::refreshWakePhrase, 1_000L);
        }
    }

    public static boolean showOverlayIfActive(
        Context context,
        String command,
        String source
    ) {
        if (MainActivity.isForeground()) return false;
        JarvisVoiceInteractionService service = active.get();
        if (service == null || !isActiveAssistant(context)) return false;
        if (!service.systemReady || !service.store.assistantOverlayEnabled()) {
            return false;
        }
        service.main.post(() -> service.showOverlay(command, source));
        return true;
    }

    public static void refreshWakeIfActive(Context context) {
        JarvisVoiceInteractionService service = active.get();
        if (service == null || !isActiveAssistant(context)) return;
        service.main.post(service::refreshWakePhrase);
    }

    public static void rearmWakeIfActive(Context context) {
        JarvisVoiceInteractionService service = active.get();
        if (service == null || !isActiveAssistant(context)) return;
        service.main.postDelayed(service::refreshWakePhrase, 450L);
    }

    public static boolean isActiveAssistant(Context context) {
        ComponentName component =
            new ComponentName(context, JarvisVoiceInteractionService.class);
        return VoiceInteractionService.isActiveService(context, component);
    }
}
