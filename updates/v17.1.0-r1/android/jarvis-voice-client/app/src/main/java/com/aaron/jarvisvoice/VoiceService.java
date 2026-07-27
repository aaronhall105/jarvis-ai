package com.aaron.jarvisvoice;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.os.IBinder;

public final class VoiceService extends Service implements
    SpeechLoop.Listener,
    PlaybackController.Listener,
    HaWebSocketClient.Listener {

    public static final String ACTION_START = "com.aaron.jarvisvoice.START";
    public static final String ACTION_STOP = "com.aaron.jarvisvoice.STOP";
    public static final String ACTION_STATUS = "com.aaron.jarvisvoice.STATUS";
    public static final String EXTRA_STATUS = "status";
    private static final int NOTIFICATION_ID = 1710;
    private static final String CHANNEL_ID = "jarvis_voice";
    private static final long FOLLOW_UP_MILLIS = 45_000L;

    private SecureStore store;
    private HaWebSocketClient webSocket;
    private SpeechLoop speechLoop;
    private PlaybackController playback;
    private final StringBuilder assistantSpeech = new StringBuilder();
    private String conversationId = "";
    private boolean continueConversation;
    private long followUpUntil;
    private boolean processing;

    @Override public void onCreate() {
        super.onCreate();
        store = new SecureStore(this);
        speechLoop = new SpeechLoop(this, this);
        playback = new PlaybackController(this, this);
        createNotificationChannel();
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? ACTION_START : intent.getAction();
        if (ACTION_STOP.equals(action)) {
            stopJarvis();
            return START_NOT_STICKY;
        }
        startForeground(NOTIFICATION_ID, notification("Starting Jarvis voice"));
        startJarvis();
        return START_STICKY;
    }

    private void startJarvis() {
        String url = store.baseUrl();
        String token = store.token();
        if (url.isBlank() || token.isBlank()) {
            status("Open the app and save Home Assistant connection details");
            stopSelf();
            return;
        }
        if (webSocket != null) webSocket.close();
        webSocket = new HaWebSocketClient(
            url, token, store.pipeline(), store.deviceId(), this
        );
        status("Connecting to Home Assistant");
        webSocket.connect();
        speechLoop.start();
    }

    private void stopJarvis() {
        if (webSocket != null) {
            webSocket.cancelActiveRun();
            webSocket.close();
            webSocket = null;
        }
        speechLoop.stop();
        playback.stop();
        status("Jarvis voice stopped");
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    @Override public void onTranscript(String text, boolean partial) {
        boolean speaking = playback.isPlaying();
        boolean followUp = !speaking && System.currentTimeMillis() < followUpUntil;
        TranscriptPolicy.Decision decision = TranscriptPolicy.evaluate(
            text, assistantSpeech.toString(), followUp, speaking, store.wakePhrase()
        );

        if (partial && decision.action != TranscriptPolicy.Action.STOP) return;
        if (decision.action == TranscriptPolicy.Action.IGNORE) return;

        if (decision.action == TranscriptPolicy.Action.STOP) {
            playback.stop();
            if (webSocket != null) webSocket.cancelActiveRun();
            processing = false;
            continueConversation = false;
            followUpUntil = 0;
            conversationId = "";
            assistantSpeech.setLength(0);
            status("Stopped. Say “Jarvis” for a new request");
            return;
        }

        playback.stop();
        followUpUntil = 0;
        continueConversation = false;
        processing = true;
        assistantSpeech.setLength(0);
        status("You: " + decision.command);
        if (webSocket != null) webSocket.sendCommand(decision.command, conversationId);
    }

    @Override public void onRecognizerStatus(String value) {
        if (!processing && !playback.isPlaying()) status(value);
    }

    @Override public void onConnected() {
        status("Ready — say “Jarvis” followed by a command");
    }

    @Override public void onDisconnected(String reason) {
        status("Home Assistant: " + (reason == null ? "disconnected" : reason));
    }

    @Override public void onTextDelta(String delta) {
        assistantSpeech.append(delta);
        status("Jarvis is answering");
    }

    @Override public void onIntentEnd(String speech, String newConversationId, boolean shouldContinue) {
        if (!newConversationId.isBlank()) conversationId = newConversationId;
        continueConversation = shouldContinue;
        if (assistantSpeech.length() == 0 && speech != null) assistantSpeech.append(speech);
    }

    @Override public void onTtsUrl(String url) {
        if (url == null || url.isBlank() || playback.isPlaying()) return;
        playback.play(store.baseUrl(), url, store.token());
    }

    @Override public void onRunEnded() {
        processing = false;
        if (!playback.isPlaying()) finishTurn();
    }

    @Override public void onError(String error) {
        processing = false;
        continueConversation = false;
        followUpUntil = 0;
        status("Error: " + error);
    }

    @Override public void onPlaybackStarted() {
        processing = false;
        status("Jarvis is speaking — say “Jarvis, stop” to interrupt");
    }

    @Override public void onPlaybackCompleted() {
        finishTurn();
    }

    @Override public void onPlaybackError(String error) {
        status("Audio error: " + error);
        finishTurn();
    }

    private void finishTurn() {
        processing = false;
        if (continueConversation) {
            followUpUntil = System.currentTimeMillis() + FOLLOW_UP_MILLIS;
            status("Listening for your follow-up");
        } else {
            followUpUntil = 0;
            status("Ready — say “Jarvis” for another request");
        }
    }

    private void status(String message) {
        Intent update = new Intent(ACTION_STATUS).setPackage(getPackageName()).putExtra(EXTRA_STATUS, message);
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
            .setContentTitle("Jarvis Voice")
            .setContentText(text)
            .setOngoing(true)
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
                CHANNEL_ID, "Jarvis Voice", NotificationManager.IMPORTANCE_LOW
            ));
        }
    }

    @Override public void onDestroy() {
        if (webSocket != null) webSocket.close();
        speechLoop.stop();
        playback.stop();
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent intent) { return null; }
}
