package com.aaron.jarvisvoice;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.media.AudioAttributes;
import android.media.AudioFocusRequest;
import android.media.AudioManager;
import android.os.Build;
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
    WakePhraseEngine.Listener,
    StandardSpeechEngine.Listener {

    public static final String ACTION_START = "com.aaron.jarvisvoice.START";
    public static final String ACTION_STOP = "com.aaron.jarvisvoice.STOP";
    public static final String ACTION_START_VOICE = "com.aaron.jarvisvoice.START_VOICE";
    public static final String ACTION_STOP_VOICE = "com.aaron.jarvisvoice.STOP_VOICE";
    public static final String ACTION_SEND_TEXT = "com.aaron.jarvisvoice.SEND_TEXT";
    public static final String ACTION_APPLY_SETTINGS = "com.aaron.jarvisvoice.APPLY_SETTINGS";
    public static final String ACTION_NEW_CHAT = "com.aaron.jarvisvoice.NEW_CHAT";
    public static final String ACTION_ASSISTANT_INVOKE = "com.aaron.jarvisvoice.ASSISTANT_INVOKE";
    public static final String ACTION_ASSISTANT_DISMISS = "com.aaron.jarvisvoice.ASSISTANT_DISMISS";
    public static final String ACTION_STATUS = "com.aaron.jarvisvoice.STATUS";
    public static final String ACTION_EVENT = "com.aaron.jarvisvoice.EVENT";

    public static final String EXTRA_STATUS = "status";
    public static final String EXTRA_TEXT = "text";
    public static final String EXTRA_EVENT = "event";
    public static final String EXTRA_ROLE = "role";
    public static final String EXTRA_ACTIVE = "active";
    public static final String EXTRA_LISTENING = "listening";
    public static final String EXTRA_MODE = "mode";

    private static final int NOTIFICATION_ID = 1800;
    private static final String CHANNEL_ID = "jarvis_chat_voice";

    private final Handler main = new Handler(Looper.getMainLooper());
    private SecureStore store;
    private ChatHistoryStore history;
    private JarvisRealtimeClient client;
    private RealtimeAudioEngine audio;
    private RealtimePlayback realtimePlayback;
    private PlaybackController originalPlayback;
    private HomeAssistantTtsClient homeAssistantTts;
    private WakePhraseEngine wakePhraseEngine;
    private StandardSpeechEngine standardSpeechEngine;
    private PowerManager.WakeLock wakeLock;
    private AudioManager audioManager;
    private AudioFocusRequest audioFocusRequest;

    private boolean stopping;
    private boolean ready;
    private boolean requestedVoiceActive;
    private boolean voiceActive;
    private boolean brainActive;
    private boolean playbackActive;
    private boolean microphoneForegroundActive;
    private String pendingText = "";
    private boolean pendingTextSpeak;

    @Override public void onCreate() {
        super.onCreate();
        store = new SecureStore(this);
        history = new ChatHistoryStore(this);
        audio = new RealtimeAudioEngine(this);
        realtimePlayback = new RealtimePlayback(this);
        originalPlayback = new PlaybackController(this, this);
        wakePhraseEngine = new WakePhraseEngine(this, this);
        standardSpeechEngine = new StandardSpeechEngine(this, this);
        audioManager = getSystemService(AudioManager.class);
        createNotificationChannel();
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null || intent.getAction() == null ? ACTION_START : intent.getAction();
        if (ACTION_STOP.equals(action)) {
            stopJarvis();
            return START_NOT_STICKY;
        }

        boolean microphoneForeground = promoteForeground(action);
        stopping = false;
        acquireWakeLock();

        switch (action) {
            case ACTION_START_VOICE -> {
                requestedVoiceActive = microphoneForeground;
                ensureConnected();
                if (ready && requestedVoiceActive) beginVoice();
            }
            case ACTION_STOP_VOICE -> stopVoice(false);
            case ACTION_SEND_TEXT -> {
                String text = intent.getStringExtra(EXTRA_TEXT);
                queueOrSend(text, voiceActive);
            }
            case ACTION_APPLY_SETTINGS -> reconnectForSettings();
            case ACTION_NEW_CHAT -> newChat();
            case ACTION_ASSISTANT_INVOKE -> {
                requestedVoiceActive =
                    store.assistantStartsVoice() && microphoneForeground;
                ensureConnected();
                if (ready && requestedVoiceActive) beginVoice();
                String command = intent.getStringExtra(EXTRA_TEXT);
                if (command != null && !command.isBlank()) {
                    queueOrSend(command, true);
                }
            }
            case ACTION_ASSISTANT_DISMISS -> stopVoice(false);
            default -> {
                ensureConnected();
                if (store.startWithVoice() && microphoneForeground) {
                    requestedVoiceActive = true;
                }
            }
        }

        return (store.backgroundConversations() ||
                (store.assistantWakeAlways() && JarvisVoiceInteractionService.isActiveAssistant(this)))
            ? START_STICKY
            : START_NOT_STICKY;
    }


    private boolean hasMicrophonePermission() {
        return Build.VERSION.SDK_INT < Build.VERSION_CODES.M
            || checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED;
    }

    private boolean actionNeedsMicrophone(String action) {
        if (ACTION_STOP_VOICE.equals(action)
                || ACTION_ASSISTANT_DISMISS.equals(action)) {
            return false;
        }

        return voiceActive
            || requestedVoiceActive
            || ACTION_START_VOICE.equals(action)
            || (ACTION_ASSISTANT_INVOKE.equals(action)
                && store.assistantStartsVoice())
            || (ACTION_START.equals(action) && store.startWithVoice());
    }

    private boolean promoteForeground(String action) {
        boolean wantsMicrophone = actionNeedsMicrophone(action);
        boolean microphoneGranted = hasMicrophonePermission();

        Notification activeNotification = notification(
            wantsMicrophone
                ? "Jarvis is starting voice"
                : "Jarvis is connected"
        );

        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, activeNotification);
            microphoneForegroundActive =
                wantsMicrophone && microphoneGranted;
            return microphoneForegroundActive;
        }

        int dataSyncType =
            ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC;

        if (wantsMicrophone && microphoneGranted) {
            try {
                startForeground(
                    NOTIFICATION_ID,
                    activeNotification,
                    dataSyncType
                        | ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
                );
                microphoneForegroundActive = true;
                return true;
            } catch (SecurityException denied) {
                requestedVoiceActive = false;
                voiceActive = false;
            }
        }

        startForeground(
            NOTIFICATION_ID,
            notification("Jarvis text chat is active"),
            dataSyncType
        );

        microphoneForegroundActive = false;

        if (wantsMicrophone) {
            status(
                "Open Jarvis and allow microphone access "
                    + "before starting voice"
            );
        }

        return false;
    }

    private void ensureConnected() {
        if (store.coreUrl().isBlank() || store.mobileToken().isBlank()) {
            status("Open Settings and add the Jarvis Core URL and mobile token");
            return;
        }
        if (client == null) connect();
    }

    private void connect() {
        ready = false;
        closeClientAndAudio();
        prepareOriginalVoice();
        VoiceCatalog.Entry selected = VoiceCatalog.fromId(store.voiceId());
        client = new JarvisRealtimeClient(
            store.coreUrl(),
            store.mobileToken(),
            store.deviceId(),
            store.userName(),
            VoiceCatalog.serverVoice(selected.id),
            VoiceCatalog.serverMode(selected.id),
            store.conversationMode(),
            store.vadEagerness(),
            store.conversationId(),
            this
        );
        status("Connecting to Jarvis Core");
        client.connect();
    }

    private void prepareOriginalVoice() {
        if (!VoiceCatalog.isOriginal(store.voiceId())) return;
        if (store.homeAssistantUrl().isBlank() || store.homeAssistantToken().isBlank()) {
            status("Original Jarvis assistant needs the Home Assistant URL and token");
            return;
        }
        homeAssistantTts = new HomeAssistantTtsClient(
            store.homeAssistantUrl(),
            store.homeAssistantToken(),
            store.homeAssistantPipeline(),
            store.deviceId(),
            this
        );
        homeAssistantTts.connect();
    }

    private void reconnectForSettings() {
        boolean resumeVoice = voiceActive || requestedVoiceActive;
        requestedVoiceActive = resumeVoice;
        stopCaptureAndPlayback();
        connect();
    }

    private void newChat() {
        history.clear();
        store.newConversationId();
        broadcastEvent("clear", "", "", false, false);
        requestedVoiceActive = false;
        stopCaptureAndPlayback();
        connect();
    }

    private void queueOrSend(String rawText, boolean speak) {
        String text = rawText == null ? "" : rawText.trim();
        if (text.isEmpty()) return;
        addMessage(ChatMessage.USER, text);
        pendingText = text;
        pendingTextSpeak = speak;
        ensureConnected();
        flushPendingText();
    }

    private void flushPendingText() {
        if (!ready || client == null || pendingText.isBlank()) return;
        String text = pendingText;
        boolean speak = pendingTextSpeak;
        if (client.sendText(text, speak)) {
            pendingText = "";
            pendingTextSpeak = false;
            brainActive = true;
            status("Thinking");
        }
    }

    private void beginVoice() {
        if (!ready || stopping) return;

        if (!microphoneForegroundActive
                || !hasMicrophonePermission()) {
            requestedVoiceActive = false;
            voiceActive = false;
            status(
                "Microphone permission is required. "
                    + "Open Jarvis before starting voice."
            );
            broadcastState(false, false);
            return;
        }
        requestedVoiceActive = true;
        voiceActive = true;
        wakePhraseEngine.stop();
        acquireAudioFocus();
        if (ConversationMode.STANDARD.equals(store.conversationMode())) {
            audio.stop();
            startStandardListening();
        } else {
            standardSpeechEngine.stop();
            if (!audio.isRunning()) audio.start();
            status("Live voice — listening continuously");
            broadcastState(true, true);
        }
    }

    private void startStandardListening() {
        if (!voiceActive || brainActive || playbackActive || stopping) return;
        standardSpeechEngine.start();
        status("Standard voice — listening for one message");
        broadcastState(true, true);
    }

    private void stopVoice(boolean stopService) {
        requestedVoiceActive = false;
        voiceActive = false;
        brainActive = false;
        stopCaptureAndPlayback();
        releaseAudioFocus();
        broadcastState(false, false);
        if (stopService) {
            stopJarvis();
        } else if (ready && store.wakeEnabled()) {
            armWakeWord();
        } else {
            status("Ready — type a message or tap Voice");
        }
    }

    private void armWakeWord() {
        if (!ready || stopping || voiceActive) return;
        standardSpeechEngine.stop();
        audio.stop();
        releaseAudioFocus();
        if (store.assistantWakeAlways() && JarvisVoiceInteractionService.isActiveAssistant(this)) {
            wakePhraseEngine.stop();
            JarvisVoiceInteractionService.rearmWakeIfActive(this);
            status("Always-on wake phrase armed — say “" + store.wakePhrase() + "”");
        } else {
            wakePhraseEngine.start(store.wakePhrase());
            status("Wake word armed — say “" + store.wakePhrase() + "”");
        }
        broadcastState(false, false);
    }

    private void stopCaptureAndPlayback() {
        wakePhraseEngine.stop();
        standardSpeechEngine.stop();
        audio.stop();
        realtimePlayback.interrupt();
        originalPlayback.stop();
        playbackActive = false;
        if (homeAssistantTts != null) homeAssistantTts.cancelActiveRun();
    }

    private void closeClientAndAudio() {
        stopCaptureAndPlayback();
        if (client != null) {
            client.close();
            client = null;
        }
        if (homeAssistantTts != null) {
            homeAssistantTts.close();
            homeAssistantTts = null;
        }
    }

    private void stopJarvis() {
        stopping = true;
        ready = false;
        requestedVoiceActive = false;
        voiceActive = false;
        main.removeCallbacksAndMessages(null);
        closeClientAndAudio();
        releaseAudioFocus();
        releaseWakeLock();
        status("Jarvis stopped");
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    @Override public void onConnected() {
        status("Connected — preparing Jarvis");
    }

    @Override public void onReady(
        String model,
        String voice,
        String voiceMode,
        String conversationMode,
        boolean unifiedBrain
    ) {
        ready = true;
        String mode = ConversationMode.label(conversationMode);
        status("Ready · " + mode + " · " + (unifiedBrain ? "Jarvis Core" : model));
        flushPendingText();
        if (requestedVoiceActive) {
            beginVoice();
        } else if (store.wakeEnabled()) {
            armWakeWord();
        } else {
            broadcastState(false, false);
        }
    }

    @Override public void onDisconnected(String reason) {
        ready = false;
        audio.stop();
        standardSpeechEngine.stop();
        status("Reconnecting: " + safe(reason, "connection lost"));
        broadcastState(voiceActive, false);
    }

    @Override public void onStatus(String message) {
        if (message != null && !message.isBlank()) status(message);
    }

    @Override public void onUserTranscript(String text) {
        if (text == null || text.isBlank()) return;
        brainActive = true;
        addMessage(ChatMessage.USER, text);
        status("Thinking");
    }

    @Override public void onAssistantTranscriptDelta(String text) {
        // The authoritative text stream is brain.delta. Realtime transcript is speech rendering only.
    }

    @Override public void onAssistantTranscriptDone(String text) {}

    @Override public void onAudio(byte[] pcm16) {
        if (!voiceActive) return;
        playbackActive = true;
        realtimePlayback.enqueue(pcm16);
    }

    @Override public void onSpeechStarted() {
        if (!voiceActive) return;
        brainActive = true;
        realtimePlayback.interrupt();
        originalPlayback.stop();
        playbackActive = false;
        if (homeAssistantTts != null) homeAssistantTts.cancelActiveRun();
        status("Listening");
    }

    @Override public void onAudioDone() {
        realtimePlayback.markDone();
    }

    @Override public void onBrainStarted(String command) {
        brainActive = true;
        broadcastEvent("thinking", "", "", voiceActive, false);
        status("Thinking");
    }

    @Override public void onBrainDelta(String text) {
        if (text == null || text.isEmpty()) return;
        broadcastEvent("assistant_delta", ChatMessage.ASSISTANT, text, voiceActive, false);
    }

    @Override public void onBrainResponse(String text, boolean success, String conversationId) {
        brainActive = false;
        if (text != null && !text.isBlank()) addMessage(ChatMessage.ASSISTANT, text);
        status(success ? "Jarvis answered" : "Jarvis Core returned an error");
        if (!store.keepConversationOpen() && voiceActive && !playbackActive && !VoiceCatalog.isOriginal(store.voiceId())) {
            main.postDelayed(() -> stopVoice(false), 250L);
        }
    }

    @Override public void onOriginalTts(String text) {
        if (!voiceActive || text == null || text.isBlank()) return;
        if (homeAssistantTts == null) {
            onHomeAssistantTtsError("Original Jarvis assistant is not connected to Home Assistant");
            return;
        }
        playbackActive = true;
        status("Jarvis is speaking");
        homeAssistantTts.speak(text);
    }

    @Override public void onTurnDone() {
        if (!voiceActive) return;
        if (!store.keepConversationOpen() && !playbackActive) {
            stopVoice(false);
        } else if (ConversationMode.STANDARD.equals(store.conversationMode()) &&
                   store.standardAutoListen() && !playbackActive && !brainActive) {
            main.postDelayed(this::startStandardListening, 250L);
        }
    }

    @Override public void onError(String message) {
        brainActive = false;
        status("Error: " + safe(message, "unknown voice error"));
        broadcastEvent("error", ChatMessage.SYSTEM, safe(message, "Unknown voice error"), voiceActive, false);
    }

    @Override public void onAudioFrame(byte[] pcm16) {
        JarvisRealtimeClient current = client;
        if (voiceActive && ConversationMode.LIVE.equals(store.conversationMode()) && current != null) {
            current.sendAudio(pcm16);
        }
    }

    @Override public void onInputLevel(float level) {
        broadcastEvent("level", "", Float.toString(level), voiceActive, true);
    }

    @Override public void onAudioError(String message) {
        status(message);
    }

    @Override public void onPlaybackState(boolean playing) {
        playbackActive = playing;
        if (playing) {
            status("Jarvis is speaking — you can interrupt");
            broadcastState(voiceActive, false);
            return;
        }
        afterPlayback();
    }

    @Override public void onPlaybackStarted() {
        playbackActive = true;
        status("Jarvis is speaking — you can interrupt");
        broadcastState(voiceActive, false);
    }

    @Override public void onPlaybackCompleted() {
        playbackActive = false;
        afterPlayback();
    }

    @Override public void onPlaybackError(String message) {
        playbackActive = false;
        status("Audio error: " + safe(message, "playback failed"));
        afterPlayback();
    }

    private void afterPlayback() {
        if (!voiceActive) return;
        if (!store.keepConversationOpen()) {
            stopVoice(false);
        } else if (ConversationMode.STANDARD.equals(store.conversationMode()) && store.standardAutoListen()) {
            main.postDelayed(this::startStandardListening, 250L);
        } else {
            status("Live voice — listening continuously");
            broadcastState(true, true);
        }
    }

    @Override public void onHomeAssistantTtsConnected() {
        status("Original Jarvis assistant connected");
    }

    @Override public void onHomeAssistantTtsUrl(String url) {
        if (!voiceActive) return;
        originalPlayback.play(store.homeAssistantUrl(), url, store.homeAssistantToken());
    }

    @Override public void onHomeAssistantTtsDone() {}

    @Override public void onHomeAssistantTtsError(String message) {
        playbackActive = false;
        status("Original voice error: " + safe(message, "Home Assistant TTS failed"));
        afterPlayback();
    }

    @Override public void onWakePhrase(String transcript, String command) {
        if (stopping) return;
        if (store.assistantOverlayEnabled() &&
            JarvisVoiceInteractionService.showOverlayIfActive(this, command, "wake_word")) {
            return;
        }
        requestedVoiceActive = true;
        beginVoice();
        if (command != null && !command.isBlank()) {
            queueOrSend(command, true);
        }
    }

    @Override public void onWakeStatus(String message) {
        if (!voiceActive && !stopping) status(message);
    }

    @Override public void onWakeError(String message) {
        if (!voiceActive && !stopping) status(message);
    }

    @Override public void onStandardReady() {
        status("Listening");
        broadcastState(true, true);
    }

    @Override public void onStandardPartial(String text) {
        broadcastEvent("draft", ChatMessage.USER, text, true, true);
    }

    @Override public void onStandardFinal(String text) {
        if (!voiceActive) return;
        queueOrSend(text, true);
        broadcastState(true, false);
    }

    @Override public void onStandardError(String message) {
        status(message);
        broadcastState(voiceActive, false);
    }

    private void addMessage(String role, String text) {
        history.add(role, text);
        broadcastEvent("message", role, text, voiceActive, false);
    }

    private void status(String message) {
        Intent update = new Intent(ACTION_STATUS)
            .setPackage(getPackageName())
            .putExtra(EXTRA_STATUS, message);
        sendBroadcast(update);
        broadcastEvent("status", "", message, voiceActive, false);
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) manager.notify(NOTIFICATION_ID, notification(message));
    }

    private void broadcastState(boolean active, boolean listening) {
        broadcastEvent("state", "", "", active, listening);
    }

    private void broadcastEvent(String event, String role, String text, boolean active, boolean listening) {
        Intent update = new Intent(ACTION_EVENT)
            .setPackage(getPackageName())
            .putExtra(EXTRA_EVENT, event)
            .putExtra(EXTRA_ROLE, role)
            .putExtra(EXTRA_TEXT, text)
            .putExtra(EXTRA_ACTIVE, active)
            .putExtra(EXTRA_LISTENING, listening)
            .putExtra(EXTRA_MODE, store == null ? ConversationMode.LIVE : store.conversationMode());
        sendBroadcast(update);
    }

    private Notification notification(String text) {
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent openPending = PendingIntent.getActivity(
            this, 1, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
        );
        Intent stopVoice = new Intent(this, VoiceService.class).setAction(ACTION_STOP_VOICE);
        PendingIntent stopVoicePending = PendingIntent.getService(
            this, 2, stopVoice, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
        );
        Intent stop = new Intent(this, VoiceService.class).setAction(ACTION_STOP);
        PendingIntent stopPending = PendingIntent.getService(
            this, 3, stop, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
        );
        return new Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(com.aaron.jarvisvoice.R.drawable.ic_jarvis)
            .setContentTitle("Jarvis")
            .setContentText(shorten(text, 120))
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(openPending)
            .addAction(new Notification.Action.Builder(
                com.aaron.jarvisvoice.R.drawable.ic_jarvis, "End voice", stopVoicePending
            ).build())
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
                "Jarvis assistant and chat",
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
                    if (change == AudioManager.AUDIOFOCUS_LOSS) stopVoice(false);
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
        wakeLock = manager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "jarvisvoice:chat");
        wakeLock.setReferenceCounted(false);
        wakeLock.acquire(12 * 60 * 60 * 1000L);
    }

    private void releaseWakeLock() {
        if (wakeLock != null && wakeLock.isHeld()) {
            try { wakeLock.release(); } catch (Exception ignored) {}
        }
        wakeLock = null;
    }

    @Override public void onDestroy() {
        stopping = true;
        closeClientAndAudio();
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
