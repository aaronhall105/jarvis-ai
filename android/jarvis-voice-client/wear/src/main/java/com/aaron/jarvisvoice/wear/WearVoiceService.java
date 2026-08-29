package com.aaron.jarvisvoice.wear;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.os.IBinder;
import com.aaron.jarvisvoice.protocol.WatchConversationState;

public final class WearVoiceService extends Service implements WearConversationController.Listener {
    public static final String ACTION_START = "com.aaron.jarvisvoice.wear.START";
    public static final String ACTION_CANCEL = "com.aaron.jarvisvoice.wear.CANCEL";
    public static final String ACTION_SEND_TEXT = "com.aaron.jarvisvoice.wear.SEND_TEXT";
    public static final String ACTION_CLEAR_CHAT = "com.aaron.jarvisvoice.wear.CLEAR_CHAT";
    public static final String ACTION_PREPARE = "com.aaron.jarvisvoice.wear.PREPARE";
    public static final String ACTION_STATE = "com.aaron.jarvisvoice.wear.STATE";
    public static final String EXTRA_STATE = "state", EXTRA_MESSAGE = "message", EXTRA_TEXT = "text", EXTRA_ROLE = "role", EXTRA_COMPLETE = "complete";
    private WearConversationController controller;
    @Override public void onCreate() {
        super.onCreate();
        controller = new WearConversationController(this, this, inactivityTimeoutMs());
    }
    @Override public int onStartCommand(Intent intent, int flags, int id) {
        if (intent != null && ACTION_CANCEL.equals(intent.getAction())) {
            controller.cancel();
            return START_NOT_STICKY;
        }
        if (intent != null && ACTION_SEND_TEXT.equals(intent.getAction())) {
            controller.sendText(intent.getStringExtra(EXTRA_TEXT));
            return START_NOT_STICKY;
        }
        if (intent != null && ACTION_CLEAR_CHAT.equals(intent.getAction())) {
            controller.clearChat();
            return START_NOT_STICKY;
        }
        if (intent != null && ACTION_PREPARE.equals(intent.getAction())) {
            controller.prepareTransport();
            return START_STICKY;
        }
        createChannel(); Intent open = new Intent(this, JarvisWearActivity.class); PendingIntent pending = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE);
        Notification notification = new Notification.Builder(this, "jarvis_wear_voice").setSmallIcon(android.R.drawable.ic_btn_speak_now).setContentTitle("Jarvis").setContentText("Watch conversation active").setContentIntent(pending).setOngoing(true).build();
        startForeground(41, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE); controller.start(); return START_NOT_STICKY;
    }
    private void createChannel() { getSystemService(NotificationManager.class).createNotificationChannel(new NotificationChannel("jarvis_wear_voice", "Jarvis voice", NotificationManager.IMPORTANCE_LOW)); }
    @Override public void onState(WatchConversationState state, String message) {
        if (state == WatchConversationState.IDLE) stopForeground(STOP_FOREGROUND_REMOVE);
        sendBroadcast(new Intent(ACTION_STATE).setPackage(getPackageName()).putExtra(EXTRA_STATE, state.name()).putExtra(EXTRA_MESSAGE, message));
    }
    @Override public void onTranscript(String role, String text, boolean complete) { sendBroadcast(new Intent(ACTION_STATE).setPackage(getPackageName()).putExtra(EXTRA_ROLE, role).putExtra(EXTRA_TEXT, text).putExtra(EXTRA_COMPLETE, complete)); }
    @Override public void onEnded() { stopForeground(STOP_FOREGROUND_REMOVE); stopSelf(); }
    @Override public void onDestroy() { controller.close(); super.onDestroy(); }
    @Override public IBinder onBind(Intent intent) { return null; }
    private long inactivityTimeoutMs() {
        try {
            ApplicationInfo info = getPackageManager().getApplicationInfo(
                getPackageName(),
                PackageManager.GET_META_DATA
            );
            Object raw = info.metaData.get("com.aaron.jarvisvoice.WATCH_INACTIVITY_TIMEOUT_MS");
            long configured = raw instanceof Number number ? number.longValue() : 8_000L;
            return Math.max(3_000L, Math.min(configured, 60_000L));
        } catch (Exception ignored) {
            return 8_000L;
        }
    }
}
