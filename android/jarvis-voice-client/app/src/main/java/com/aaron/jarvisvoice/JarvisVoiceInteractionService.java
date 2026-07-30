package com.aaron.jarvisvoice;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.service.voice.VoiceInteractionService;

import java.lang.ref.WeakReference;

public final class JarvisVoiceInteractionService extends VoiceInteractionService {
    public static final String ARG_COMMAND = "jarvis_command";
    public static final String ARG_SOURCE = "jarvis_source";

    private static WeakReference<JarvisVoiceInteractionService> active =
        new WeakReference<>(null);

    private final Handler main = new Handler(Looper.getMainLooper());
    private SecureStore store;
    private boolean systemReady;

    @Override public void onCreate() {
        super.onCreate();
        store = new SecureStore(this);
    }

    @Override public void onReady() {
        super.onReady();
        systemReady = true;
        active = new WeakReference<>(this);
        requestWakeService();
    }

    @Override public void onShutdown() {
        systemReady = false;
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
        JarvisVoiceInteractionService current = active.get();
        if (current == this) active = new WeakReference<>(null);
        super.onDestroy();
    }

    private void requestWakeService() {
        store = new SecureStore(this);
        if (!systemReady) return;

        String action = store.wakeEnabled()
            ? VoiceService.ACTION_ARM_WAKE
            : VoiceService.ACTION_START;

        try {
            startForegroundService(
                new Intent(this, VoiceService.class).setAction(action)
            );
        } catch (Exception ignored) {}
    }

    private boolean showOverlay(String command, String source) {
        if (MainActivity.isVisible()) return false;
        if (!systemReady || !store.assistantOverlayEnabled()) return false;

        VoiceSessionState.setActive(true);
        Bundle args = new Bundle();
        args.putString(ARG_COMMAND, command == null ? "" : command.trim());
        args.putString(ARG_SOURCE, source == null ? "assistant" : source);
        showSession(args, 0);
        return true;
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

        service.main.post(() -> service.showOverlay(command, source));
        return true;
    }

    public static void refreshWakeIfActive(Context context) {
        requestWakeIfActive(context, 0L);
    }

    public static void ensureWakeIfActive(Context context) {
        requestWakeIfActive(context, 0L);
    }

    public static void rearmWakeIfActive(Context context) {
        requestWakeIfActive(context, 350L);
    }

    private static void requestWakeIfActive(Context context, long delay) {
        JarvisVoiceInteractionService service = active.get();
        if (service == null || !isActiveAssistant(context)) return;
        service.main.postDelayed(service::requestWakeService, delay);
    }

    public static boolean isActiveAssistant(Context context) {
        ComponentName component = new ComponentName(
            context,
            JarvisVoiceInteractionService.class
        );
        return VoiceInteractionService.isActiveService(context, component);
    }
}
