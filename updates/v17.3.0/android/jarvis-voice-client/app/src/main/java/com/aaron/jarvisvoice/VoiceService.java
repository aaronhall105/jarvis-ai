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
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;

public final class VoiceService extends Service implements
    JarvisRealtimeClient.Listener,
    RealtimeAudioEngine.Listener,
    RealtimePlayback.Listener,
    PlaybackController.Listener,
    HomeAssistantTtsClient.Listener,
    WakePhraseEngine.Listener {

    public static final String ACTION_START = "com.aaron.jarvisvoice.START";
    public static final String ACTION_STOP = "com.aaron.jarvisvoice.STOP";
    public static final String ACTION_STATUS = "com.aaron.jarvisvoice.STATUS";
    public static final String EXTRA_STATUS = "status";
    private static final int NOTIFICATION_ID = 1730;
    private static final String CHANNEL_ID = "jarvis_unified_voice";
    private static final long FOLLOW_UP_MILLIS = 45_000L;

    private final Handler main = new Handler(Looper.getMainLooper());
    private SecureStore store;
    private JarvisRealtimeClient client;
    private RealtimeAudioEngine audio;
    private RealtimePlayback realtimePlayback;
    private PlaybackController originalPlayback;
    private HomeAssistantTtsClient homeAssistantTts;
    private WakePhraseEngine wakePhraseEngine;
    private PowerManager.WakeLock wakeLock;
    private AudioManager audioManager;
    private AudioFocusRequest audioFocusRequest;
    private final StringBuilder assistantTranscript = new StringBuilder();
    private boolean stopping;
    private boolean liveSession;
    private boolean brainActive;
    private String pendingWakeCommand = "";

    private final Runnable returnToWakeMode = () -> {
        if (!stopping && liveSession && store.wakeEnabled()) {
            armWakeWord();
        }
    };

    @Override public void onCreate() {
        super.onCreate();
        store = new SecureStore(this);
        audio = new RealtimeAudioEngine(this);
        realtimePlayback = new RealtimePlayback(this);
        originalPlayback = new PlaybackController(this, this);
        wakePhraseEngine = new WakePhraseEngine(this, this);
        audioManager = getSystemService(AudioManager.class);
        createNotificationChannel();
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? ACTION_START : intent.getAction();
        if (ACTION_STOP.equals(action)) {
            stopJarvis();
            return START_NOT_STICKY;
        }
        startForeground(NOTIFICATION_ID, notification("Starting Jarvis unified voice"));
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
        if (VoiceCatalog.isOriginal(store.voiceId()) &&
            (store.homeAssistantUrl().isBlank() || store.homeAssistantToken().isBlank())) {
            status("Original Jarvis voice needs the Home Assistant URL and token");
            stopSelf();
            return;
        }
        acquireWakeLock();
        if (store.wakeEnabled()) {
            armWakeWord();
        } else {
            startLiveSession("");
        }
    }

    private void armWakeWord() {
        main.removeCallbacks(returnToWakeMode);
        liveSession = false;
        brainActive = false;
        pendingWakeCommand = "";
        assistantTranscript.setLength(0);
        closeLiveConnections();
        releaseAudioFocus();
        wakePhraseEngine.start(store.wakePhrase());
        status("Armed — say “" + store.wakePhrase() + "”");
    }

    private void startLiveSession(String commandFromWakePhrase) {
        main.removeCallbacks(returnToWakeMode);
        wakePhraseEngine.stop();
        liveSession = true;
        brainActive = false;
        pendingWakeCommand = commandFromWakePhrase == null ? "" : commandFromWakePhrase.trim();
        assistantTranscript.setLength(0);
        acquireAudioFocus();

        VoiceCatalog.Entry selected = VoiceCatalog.fromId(store.voiceId());
        if (VoiceCatalog.MODE_HOME_ASSISTANT.equals(selected.mode)) {
            homeAssistantTts = new HomeAssistantTtsClient(
                store.homeAssistantUrl(),
                store.homeAssistantToken(),
                store.homeAssistantPipeline(),
                store.deviceId(),
                this
            );
            homeAssistantTts.connect();
        }

        if (client != null) client.close();
        client = new JarvisRealtimeClient(
            store.coreUrl(),
            store.mobileToken(),
            store.deviceId(),
            store.userName(),
            VoiceCatalog.serverVoice(selected.id),
            VoiceCatalog.serverMode(selected.id),
            this
        );
        status("Connecting to Jarvis Core");
        client.connect();
    }

    private void stopJarvis() {
        stopping = true;
        liveSession = false;
        main.removeCallbacksAndMessages(null);
        wakePhraseEngine.stop();
        closeLiveConnections();
        releaseAudioFocus();
        releaseWakeLock();
        status("Jarvis unified voice stopped");
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    private void closeLiveConnections() {
        if (client != null) {
            client.close();
            client = null;
        }
        audio.stop();
        realtimePlayback.interrupt();
        originalPlayback.stop();
        if (homeAssistantTts != null) {
            homeAssistantTts.close();
            homeAssistantTts = null;
        }
    }

    private void scheduleFollowUpWindow() {
        if (!liveSession || stopping || !store.wakeEnabled()) return;
        main.removeCallbacks(returnToWakeMode);
        main.postDelayed(returnToWakeMode, FOLLOW_UP_MILLIS);
        status("Listening for your follow-up — no wake word needed");
    }

    @Override public void onConnected() {
        if (liveSession) status("Connected — preparing unified Jarvis session");
    }

    @Override public void onReady(String model, String voice, String voiceMode, boolean unifiedBrain) {
        if (!liveSession) return;
        assistantTranscript.setLength(0);
        if (!audio.isRunning()) audio.start();
        String brain = unifiedBrain ? "Jarvis Core brain connected" : "voice session ready";
        status("Live — " + brain + " · " + (voiceMode == null || voiceMode.isBlank() ? voice : voiceMode));
        String queued = pendingWakeCommand;
        pendingWakeCommand = "";
        if (!queued.isBlank() && client != null) {
            brainActive = true;
            client.sendText(queued);
        } else {
            scheduleFollowUpWindow();
        }
    }

    @Override public void onDisconnected(String reason) {
        audio.stop();
        if (!stopping && liveSession) status("Reconnecting: " + safe(reason, "connection lost"));
    }

    @Override public void onStatus(String message) {
        if (message != null && !message.isBlank() && liveSession) status(message);
    }

    @Override public void onUserTranscript(String text) {
        if (text == null || text.isBlank()) return;
        main.removeCallbacks(returnToWakeMode);
        brainActive = true;
        status("You: " + shorten(text, 180));
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
        realtimePlayback.enqueue(pcm16);
    }

    @Override public void onSpeechStarted() {
        main.removeCallbacks(returnToWakeMode);
        brainActive = true;
        realtimePlayback.interrupt();
        originalPlayback.stop();
        if (homeAssistantTts != null) homeAssistantTts.cancelActiveRun();
        status("Listening — interruption accepted");
    }

    @Override public void onAudioDone() {
        realtimePlayback.markDone();
    }

    @Override public void onBrainStarted(String command) {
        main.removeCallbacks(returnToWakeMode);
        brainActive = true;
        status("Jarvis Core: " + shorten(command, 160));
    }

    @Override public void onBrainResponse(String text, boolean success) {
        brainActive = false;
        if (text != null && !text.isBlank()) {
            status((success ? "Jarvis Core answered: " : "Jarvis Core error: ") + shorten(text, 170));
        }
    }

    @Override public void onOriginalTts(String text) {
        if (!liveSession || text == null || text.isBlank()) return;
        if (homeAssistantTts == null) {
            onHomeAssistantTtsError("Original Jarvis voice is not connected to Home Assistant");
            return;
        }
        status("Generating original Jarvis voice");
        homeAssistantTts.speak(text);
    }

    @Override public void onTurnDone() {
        if (!VoiceCatalog.isOriginal(store.voiceId()) && !brainActive) {
            scheduleFollowUpWindow();
        }
    }

    @Override public void onError(String message) {
        brainActive = false;
        status("Error: " + safe(message, "unknown unified voice error"));
        scheduleFollowUpWindow();
    }

    @Override public void onAudioFrame(byte[] pcm16) {
        JarvisRealtimeClient current = client;
        if (liveSession && current != null) current.sendAudio(pcm16);
    }

    @Override public void onInputLevel(float level) {
        // Reserved for visual diagnostics without notification churn.
    }

    @Override public void onAudioError(String message) {
        status(message);
    }

    @Override public void onPlaybackState(boolean playing) {
        if (playing) {
            main.removeCallbacks(returnToWakeMode);
            status("Jarvis is speaking — talk to interrupt");
        } else if (liveSession && !brainActive && !VoiceCatalog.isOriginal(store.voiceId())) {
            scheduleFollowUpWindow();
        }
    }

    @Override public void onPlaybackStarted() {
        main.removeCallbacks(returnToWakeMode);
        status("Original Jarvis voice is speaking — talk to interrupt");
    }

    @Override public void onPlaybackCompleted() {
        scheduleFollowUpWindow();
    }

    @Override public void onPlaybackError(String message) {
        brainActive = false;
        status("Audio error: " + safe(message, "playback failed"));
        scheduleFollowUpWindow();
    }

    @Override public void onHomeAssistantTtsConnected() {
        if (liveSession && VoiceCatalog.isOriginal(store.voiceId())) {
            status("Original Jarvis voice connected");
        }
    }

    @Override public void onHomeAssistantTtsUrl(String url) {
        if (!liveSession) return;
        originalPlayback.play(store.homeAssistantUrl(), url, store.homeAssistantToken());
    }

    @Override public void onHomeAssistantTtsDone() {
        // MediaPlayer completion is authoritative because the HA run can finish first.
    }

    @Override public void onHomeAssistantTtsError(String message) {
        brainActive = false;
        status("Original voice error: " + safe(message, "Home Assistant TTS failed"));
        scheduleFollowUpWindow();
    }

    @Override public void onWakePhrase(String transcript, String command) {
        if (stopping) return;
        status("Wake word heard: " + shorten(transcript, 120));
        startLiveSession(command);
    }

    @Override public void onWakeStatus(String message) {
        if (!liveSession && !stopping) status(message);
    }

    @Override public void onWakeError(String message) {
        if (!liveSession && !stopping) status(message);
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
            .setContentTitle("Jarvis Unified Voice")
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
                "Jarvis Unified Voice",
                NotificationManager.IMPORTANCE_LOW
            ));
        }
    }

    private void acquireAudioFocus() {
        if (audioManager == null || audioFocusRequest != null) return;
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
                    if (change == AudioManager.AUDIOFOCUS_LOSS && liveSession) {
                        if (store.wakeEnabled()) armWakeWord();
                        else stopJarvis();
                    }
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
        wakeLock = manager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "jarvisvoice:unified");
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
        liveSession = false;
        main.removeCallbacksAndMessages(null);
        wakePhraseEngine.stop();
        closeLiveConnections();
        realtimePlayback.close();
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
