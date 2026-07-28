package com.aaron.jarvisvoice;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.media.AudioAttributes;
import android.media.AudioFocusRequest;
import android.media.AudioManager;
import android.os.IBinder;
import android.os.PowerManager;

public final class VoiceService extends Service implements
    JarvisRealtimeClient.Listener,
    RealtimeAudioEngine.Listener,
    RealtimePlayback.Listener {

    public static final String ACTION_START = "com.aaron.jarvisvoice.START";
    public static final String ACTION_STOP = "com.aaron.jarvisvoice.STOP";
    public static final String ACTION_STATUS = "com.aaron.jarvisvoice.STATUS";
    public static final String EXTRA_STATUS = "status";
    private static final int NOTIFICATION_ID = 1720;
    private static final String CHANNEL_ID = "jarvis_realtime_voice";

    private SecureStore store;
    private JarvisRealtimeClient client;
    private RealtimeAudioEngine audio;
    private RealtimePlayback playback;
    private PowerManager.WakeLock wakeLock;
    private AudioManager audioManager;
    private AudioFocusRequest audioFocusRequest;
    private final StringBuilder assistantTranscript = new StringBuilder();
    private boolean stopping;

    @Override public void onCreate() {
        super.onCreate();
        store = new SecureStore(this);
        audio = new RealtimeAudioEngine(this);
        playback = new RealtimePlayback(this);
        audioManager = getSystemService(AudioManager.class);
        createNotificationChannel();
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? ACTION_START : intent.getAction();
        if (ACTION_STOP.equals(action)) {
            stopJarvis();
            return START_NOT_STICKY;
        }
        startForeground(NOTIFICATION_ID, notification("Starting realtime voice"));
        startJarvis();
        return START_STICKY;
    }

    private void startJarvis() {
        stopping = false;
        if (store.coreUrl().isBlank() || store.mobileToken().isBlank()) {
            status("Open the app and save the Jarvis Core URL and mobile token");
            stopSelf();
            return;
        }
        acquireWakeLock();
        acquireAudioFocus();
        if (client != null) client.close();
        client = new JarvisRealtimeClient(
            store.coreUrl(),
            store.mobileToken(),
            store.deviceId(),
            store.userName(),
            this
        );
        status("Connecting to Jarvis Core");
        client.connect();
    }

    private void stopJarvis() {
        stopping = true;
        if (client != null) {
            client.close();
            client = null;
        }
        audio.stop();
        playback.close();
        releaseAudioFocus();
        releaseWakeLock();
        status("Jarvis realtime voice stopped");
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    @Override public void onConnected() {
        status("Connected — preparing realtime session");
    }

    @Override public void onReady(String model) {
        assistantTranscript.setLength(0);
        if (!audio.isRunning()) audio.start();
        status("Live — talk naturally" + (model == null || model.isBlank() ? "" : " · " + model));
    }

    @Override public void onDisconnected(String reason) {
        audio.stop();
        if (!stopping) status("Reconnecting: " + safe(reason, "connection lost"));
    }

    @Override public void onStatus(String message) {
        if (message != null && !message.isBlank()) status(message);
    }

    @Override public void onUserTranscript(String text) {
        if (text != null && !text.isBlank()) status("You: " + shorten(text, 180));
    }

    @Override public void onAssistantTranscriptDelta(String text) {
        if (text == null || text.isEmpty()) return;
        assistantTranscript.append(text);
    }

    @Override public void onAssistantTranscriptDone(String text) {
        String complete = text == null || text.isBlank() ? assistantTranscript.toString() : text;
        assistantTranscript.setLength(0);
        if (!complete.isBlank()) status("Jarvis: " + shorten(complete, 180));
    }

    @Override public void onAudio(byte[] pcm16) {
        playback.enqueue(pcm16);
    }

    @Override public void onSpeechStarted() {
        playback.interrupt();
        status("Listening — interruption accepted");
    }

    @Override public void onAudioDone() {
        playback.markDone();
    }

    @Override public void onToolStarted(String command) {
        status("Jarvis Core: " + shorten(command, 160));
    }

    @Override public void onError(String message) {
        status("Error: " + safe(message, "unknown realtime voice error"));
    }

    @Override public void onAudioFrame(byte[] pcm16) {
        JarvisRealtimeClient current = client;
        if (current != null) current.sendAudio(pcm16);
    }

    @Override public void onInputLevel(float level) {
        // Kept for the on-screen diagnostics phase. Avoid notification churn here.
    }

    @Override public void onAudioError(String message) {
        status(message);
    }

    @Override public void onPlaybackState(boolean playing) {
        if (playing) status("Jarvis is speaking — talk to interrupt");
    }

    @Override public void onPlaybackError(String message) {
        status(message);
    }

    private void status(String message) {
        Intent update = new Intent(ACTION_STATUS)
            .setPackage(getPackageName())
            .putExtra(EXTRA_STATUS, message);
        sendBroadcast(update);
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) manager.notify(NOTIFICATION_ID, notification(message));
    }

    private Notification notification(String text) {
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent openPending = PendingIntent.getActivity(
            this, 1, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
        );
        Intent stop = new Intent(this, VoiceService.class).setAction(ACTION_STOP);
        PendingIntent stopPending = PendingIntent.getService(
            this, 2, stop, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
        );
        return new Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(com.aaron.jarvisvoice.R.drawable.ic_jarvis)
            .setContentTitle("Jarvis Realtime Voice")
            .setContentText(shorten(text, 120))
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(openPending)
            .addAction(new Notification.Action.Builder(
                com.aaron.jarvisvoice.R.drawable.ic_jarvis, "Stop", stopPending
            ).build())
            .build();
    }

    private void createNotificationChannel() {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) {
            manager.createNotificationChannel(new NotificationChannel(
                CHANNEL_ID,
                "Jarvis Realtime Voice",
                NotificationManager.IMPORTANCE_LOW
            ));
        }
    }

    private void acquireAudioFocus() {
        if (audioManager == null) return;
        try { audioManager.setMode(AudioManager.MODE_IN_COMMUNICATION); } catch (Exception ignored) {}
        try {
            AudioAttributes attributes = new AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                .build();
            audioFocusRequest = new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_EXCLUSIVE)
                .setAudioAttributes(attributes)
                .setAcceptsDelayedFocusGain(false)
                .setOnAudioFocusChangeListener(change -> {
                    if (change == AudioManager.AUDIOFOCUS_LOSS) stopJarvis();
                })
                .build();
            audioManager.requestAudioFocus(audioFocusRequest);
        } catch (Exception ignored) {}
    }

    private void releaseAudioFocus() {
        if (audioManager == null) return;
        try {
            if (audioFocusRequest != null) audioManager.abandonAudioFocusRequest(audioFocusRequest);
        } catch (Exception ignored) {}
        audioFocusRequest = null;
        try { audioManager.setMode(AudioManager.MODE_NORMAL); } catch (Exception ignored) {}
    }

    private void acquireWakeLock() {
        if (wakeLock != null && wakeLock.isHeld()) return;
        PowerManager manager = getSystemService(PowerManager.class);
        if (manager == null) return;
        wakeLock = manager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "jarvisvoice:realtime");
        wakeLock.setReferenceCounted(false);
        wakeLock.acquire(6 * 60 * 60 * 1000L);
    }

    private void releaseWakeLock() {
        if (wakeLock != null && wakeLock.isHeld()) {
            try { wakeLock.release(); } catch (Exception ignored) {}
        }
        wakeLock = null;
    }

    @Override public void onDestroy() {
        stopping = true;
        if (client != null) client.close();
        audio.stop();
        playback.close();
        releaseAudioFocus();
        releaseWakeLock();
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent intent) {
        return null;
    }

    private static String shorten(String value, int max) {
        String text = value == null ? "" : value.trim();
        if (text.length() <= max) return text;
        return text.substring(0, Math.max(1, max - 1)) + "…";
    }

    private static String safe(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }
}
