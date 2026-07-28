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
import android.service.voice.VoiceInteractionSession;

import java.lang.ref.WeakReference;

/** System-selected Android assistant and always-available wake-phrase host. */
public final class JarvisVoiceInteractionService extends VoiceInteractionService
    implements WakePhraseEngine.Listener {

    public static final String ARG_COMMAND = "jarvis_command";
    public static final String ARG_SOURCE = "jarvis_source";

    private static WeakReference<JarvisVoiceInteractionService> active = new WeakReference<>(null);

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
        armWakePhrase();
    }

    @Override public void onShutdown() {
        systemReady = false;
        stopWakePhrase();
        JarvisVoiceInteractionService current = active.get();
        if (current == this) active = new WeakReference<>(null);
        try {
            startService(new Intent(this, VoiceService.class).setAction(VoiceService.ACTION_STOP));
        } catch (Exception ignored) {}
        super.onShutdown();
    }

    private void warmJarvisCore() {
        if (!store.hasMobileToken() || store.coreUrl().isBlank()) return;
        try {
            startForegroundService(new Intent(this, VoiceService.class).setAction(VoiceService.ACTION_START));
        } catch (Exception ignored) {}
    }

    private void armWakePhrase() {
        main.post(() -> {
            if (!systemReady || !store.assistantWakeAlways() || !store.wakeEnabled()) return;
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) return;
            if (!store.hasMobileToken() || store.coreUrl().isBlank()) return;
            wakePhraseEngine.start(store.wakePhrase());
        });
    }

    private void stopWakePhrase() {
        main.removeCallbacksAndMessages(null);
        if (wakePhraseEngine != null) wakePhraseEngine.stop();
    }

    @Override public void onWakePhrase(String transcript, String command) {
        showOverlay(command, "wake_word");
    }

    @Override public void onWakeStatus(String message) {
        Intent update = new Intent(VoiceService.ACTION_EVENT)
            .setPackage(getPackageName())
            .putExtra(VoiceService.EXTRA_EVENT, "status")
            .putExtra(VoiceService.EXTRA_TEXT, message)
            .putExtra(VoiceService.EXTRA_ACTIVE, false)
            .putExtra(VoiceService.EXTRA_LISTENING, true);
        sendBroadcast(update);
    }

    @Override public void onWakeError(String message) {
        Intent update = new Intent(VoiceService.ACTION_EVENT)
            .setPackage(getPackageName())
            .putExtra(VoiceService.EXTRA_EVENT, "error")
            .putExtra(VoiceService.EXTRA_TEXT, message)
            .putExtra(VoiceService.EXTRA_ACTIVE, false)
            .putExtra(VoiceService.EXTRA_LISTENING, false);
        sendBroadcast(update);
        main.postDelayed(this::armWakePhrase, 1500L);
    }

    private void showOverlay(String command, String source) {
        if (!systemReady || !store.assistantOverlayEnabled()) return;
        stopWakePhrase();
        Bundle args = new Bundle();
        args.putString(ARG_COMMAND, command == null ? "" : command.trim());
        args.putString(ARG_SOURCE, source == null ? "assistant" : source);
        showSession(args, 0);
    }

    public static boolean showOverlayIfActive(Context context, String command, String source) {
        JarvisVoiceInteractionService service = active.get();
        if (service == null || !isActiveAssistant(context)) return false;
        service.main.post(() -> service.showOverlay(command, source));
        return true;
    }

    public static void rearmWakeIfActive(Context context) {
        JarvisVoiceInteractionService service = active.get();
        if (service == null || !isActiveAssistant(context)) return;
        service.main.postDelayed(service::armWakePhrase, 350L);
    }

    public static boolean isActiveAssistant(Context context) {
        ComponentName component = new ComponentName(context, JarvisVoiceInteractionService.class);
        return VoiceInteractionService.isActiveService(context, component);
    }
}
