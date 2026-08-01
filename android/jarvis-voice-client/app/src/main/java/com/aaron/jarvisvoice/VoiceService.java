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
import android.graphics.BitmapFactory;
import android.media.AudioAttributes;
import android.media.AudioFocusRequest;
import android.media.AudioManager;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;

public final class VoiceService extends Service implements
    JarvisRealtimeClient.Listener,
    RealtimeAudioEngine.Listener,
    RealtimePlayback.Listener,
    PlaybackController.Listener,
    HomeAssistantTtsClient.Listener,
    WakePhraseEngine.Listener,
    StandardSpeechEngine.Listener,
    ReliableSpeechFallback.Listener {

    private AudioRouteMonitor alpha6AudioRouteMonitor;

    public static final String ACTION_START = "com.aaron.jarvisvoice.START";
    public static final String ACTION_STOP = "com.aaron.jarvisvoice.STOP";
    public static final String ACTION_START_VOICE = "com.aaron.jarvisvoice.START_VOICE";
    public static final String ACTION_ARM_WAKE = "com.aaron.jarvisvoice.ARM_WAKE";
    public static final String ACTION_STOP_VOICE = "com.aaron.jarvisvoice.STOP_VOICE";
    public static final String ACTION_SEND_TEXT = "com.aaron.jarvisvoice.SEND_TEXT";
    public static final String ACTION_APPLY_SETTINGS = "com.aaron.jarvisvoice.APPLY_SETTINGS";
    public static final String ACTION_NEW_CHAT = "com.aaron.jarvisvoice.NEW_CHAT";
    public static final String ACTION_DELETE_CHAT = "com.aaron.jarvisvoice.DELETE_CHAT";
    public static final String ACTION_SWITCH_CHAT = "com.aaron.jarvisvoice.SWITCH_CHAT";
    public static final String ACTION_CANCEL_RESPONSE = "com.aaron.jarvisvoice.CANCEL_RESPONSE";
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
    public static final String EXTRA_CONVERSATION_ID =
        "conversation_id";

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
    private ReliableSpeechFallback speechFallback;
    private PowerManager.WakeLock wakeLock;
    private AudioManager audioManager;
    private VoiceFoundationStateMachine voiceFoundation;
    private AudioFocusRequest audioFocusRequest;

    private boolean stopping;
    private boolean ready;
    private boolean requestedVoiceActive;
    private boolean voiceActive;
    private boolean brainActive;
    private boolean playbackActive;
    private boolean microphoneForegroundActive;
    private boolean endConversationAfterReply;
    private boolean turnShouldSpeak;
    private boolean turnReceivedRealtimeAudio;
    private boolean fallbackPending;
    private boolean fallbackSpeaking;
    private String pendingText = "";
    private boolean pendingTextSpeak;
    private String lastAssistantResponse = "";
    private long echoSuppressionUntilMs;
    private String pendingBargeInPartial = "";
    private long pendingBargeInPartialAtMs;
    private long confirmedBargeInUntilMs;
    private boolean wakeOwnedVoiceSession;
    private long followUpOwnerUntilMs;

    @Override public void onCreate() {
        super.onCreate();
        new VoiceDiagnosticsStore(this).recordLifecycle("Service started", true);
        alpha6AudioRouteMonitor = new AudioRouteMonitor(this);
        alpha6AudioRouteMonitor.start();
        store = new SecureStore(this);
        voiceFoundation =
            new VoiceFoundationStateMachine(this);
        history = new ChatHistoryStore(this);
        audio = new RealtimeAudioEngine(this);
        realtimePlayback = new RealtimePlayback(this);
        originalPlayback = new PlaybackController(this, this);
        wakePhraseEngine = new WakePhraseEngine(this, this);
        standardSpeechEngine = new StandardSpeechEngine(this, this);
        speechFallback = new ReliableSpeechFallback(this, this);
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
            case ACTION_ARM_WAKE -> {
                requestedVoiceActive = false;
                ensureConnected();
                if (ready) armWakeWord();
            }
            case ACTION_START_VOICE -> {
                wakeOwnedVoiceSession = false;
                followUpOwnerUntilMs = 0L;
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
            case ACTION_DELETE_CHAT -> deleteCurrentChat();
            case ACTION_SWITCH_CHAT -> switchChat(
                intent.getStringExtra(
                    EXTRA_CONVERSATION_ID
                )
            );
            case ACTION_CANCEL_RESPONSE ->
                cancelCurrentResponse();
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

        return (
            store.backgroundConversations()
                || wakeWordUsesVoiceService()
                || (
                    store.assistantWakeAlways()
                        && JarvisVoiceInteractionService.isActiveAssistant(this)
                )
        )
            ? START_STICKY
            : START_NOT_STICKY;
    }


    private boolean hasMicrophonePermission() {
        return Build.VERSION.SDK_INT < Build.VERSION_CODES.M
            || checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED;
    }

    private boolean wakeWordUsesVoiceService() {
        return store.wakeEnabled();
    }


    private boolean actionNeedsMicrophone(String action) {
        boolean wakeWordActive =
            wakePhraseEngine != null && wakePhraseEngine.isRunning();

        return wakeWordUsesVoiceService()
            || wakeWordActive
            || voiceActive
            || requestedVoiceActive
            || ACTION_ARM_WAKE.equals(action)
            || ACTION_START_VOICE.equals(action)
            || (
                ACTION_ASSISTANT_INVOKE.equals(action)
                    && store.assistantStartsVoice()
            )
            || (
                ACTION_START.equals(action)
                    && store.startWithVoice()
            );
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
        history.ensureActiveConversation();
        ready = false;
        voiceFoundation.opening("connecting to Jarvis Core");
        closeClientAndAudio();
        prepareOriginalVoice();
        VoiceCatalog.Entry selected = VoiceCatalog.fromId(store.voiceId());
        client = new JarvisRealtimeClient(
            this,
            store.coreUrl(),
            store.mobileToken(),
            store.deviceId(),
            store.userId(),
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
        String id = history.createConversation();
        switchChat(id);
    }

    private void deleteCurrentChat() {
        String current =
            history.activeConversationId();

        if (!history.deleteConversation(current)) {
            status("Unable to delete the current chat");
            return;
        }

        requestedVoiceActive = false;
        voiceActive = false;
        wakeOwnedVoiceSession = false;
        followUpOwnerUntilMs = 0L;
        VoiceSessionState.setActive(false);
        stopCaptureAndPlayback();

        broadcastEvent(
            "clear",
            "",
            "",
            false,
            false
        );

        connect();
    }

    private void switchChat(String id) {
        if (!history.switchConversation(id)) {
            status("Unable to open that conversation");
            return;
        }
        requestedVoiceActive = false;
        voiceActive = false;
        VoiceSessionState.setActive(false);
        stopCaptureAndPlayback();
        broadcastEvent(
            "chat.switched",
            "",
            history.activeTitle(),
            false,
            false
        );
        connect();
    }

    private void cancelCurrentResponse() {
        JarvisRealtimeClient current = client;
        if (current != null) current.cancelResponse();
        pendingText = "";
        pendingTextSpeak = false;
        brainActive = false;
        fallbackPending = false;
        fallbackSpeaking = false;
        turnShouldSpeak = false;
        turnReceivedRealtimeAudio = false;
        realtimePlayback.interrupt();
        originalPlayback.stop();
        if (homeAssistantTts != null) {
            homeAssistantTts.cancelActiveRun();
        }
        if (speechFallback != null) {
            speechFallback.cancel();
        }
        playbackActive = false;
        broadcastEvent(
            "generation.cancelled",
            "",
            "",
            voiceActive,
            false
        );
        status("Stopped");
        if (
            voiceActive
                && ConversationMode.STANDARD.equals(
                    store.conversationMode()
                )
                && store.standardAutoListen()
        ) {
            main.postDelayed(
                this::startStandardListening,
                200L
            );
        }
    }

    private void queueOrSend(String rawText, boolean speak) {
        String text = rawText == null ? "" : rawText.trim();
        if (text.isEmpty()) return;
        prepareSpokenTurn(speak);
        addMessage(ChatMessage.USER, text);
        if (ConversationEndPolicy.shouldEnd(text)) {
            endConversationAfterReply = true;
        }
        pendingText = text;
        pendingTextSpeak = speak;
        ensureConnected();
        flushPendingText();
    }

    private void prepareSpokenTurn(boolean speak) {
        turnShouldSpeak = speak;
        turnReceivedRealtimeAudio = false;
        fallbackPending = false;
        fallbackSpeaking = false;
        if (speechFallback != null) speechFallback.cancel();
    }

    private void flushPendingText() {
        if (!ready || client == null || pendingText.isBlank()) return;
        String text = pendingText;
        boolean speak = pendingTextSpeak;
        prepareSpokenTurn(speak);
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
            VoiceSessionState.setActive(false);
            status(
                "Microphone permission is required. "
                    + "Open Jarvis before starting voice."
            );
            broadcastState(false, false);
            return;
        }
        requestedVoiceActive = true;
        voiceActive = true;
        VoiceSessionState.setActive(true);
        if (ConversationMode.STANDARD.equals(store.conversationMode())) {
            voiceFoundation.listeningStandard(
                "voice session started"
            );
        } else {
            voiceFoundation.listeningLive(
                "voice session started"
            );
        }
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
        if (
            !voiceActive
                || stopping
                || endConversationAfterReply
        ) {
            return;
        }

        if (
            wakeOwnedVoiceSession
                && !hasInterruptibleTurn()
                && followUpOwnerUntilMs > 0L
                && SystemClock.elapsedRealtime()
                    > followUpOwnerUntilMs
        ) {
            stopVoice(false);
            return;
        }

        if (standardSpeechEngine.isRunning()) {
            return;
        }

        standardSpeechEngine.start();
        status(
            brainActive || playbackActive
                ? "Listening — interrupt anytime"
                : "Listening"
        );
        broadcastState(true, true);
    }



    private boolean hasInterruptibleTurn() {
        return brainActive
            || playbackActive
            || fallbackPending
            || fallbackSpeaking;
    }

    private boolean usesPrivateAudioRoute() {
        return alpha6AudioRouteMonitor != null
            && alpha6AudioRouteMonitor.usesPrivateListeningRoute();
    }

    private long bargeInArmDelayMs() {
        return usesPrivateAudioRoute()
            ? 160L
            : 480L;
    }

    private long followUpListenDelayMs() {
        return usesPrivateAudioRoute()
            ? 120L
            : 160L;
    }

    private boolean explicitSpeakerBargeIn(String candidate) {
        String normalised = PlaybackEchoPolicy.normalise(candidate);
        return normalised.matches(
            "^(?:jarvis\\s+)?(?:stop|wait|cancel|quiet|no|hold on|hang on)(?:\\s+.*)?$"
        );
    }

    private boolean isEchoSuppressionWindowActive() {
        return playbackActive
            || fallbackSpeaking
            || SystemClock.elapsedRealtime()
                < echoSuppressionUntilMs;
    }

    private boolean isLikelyPlaybackEcho(String candidate) {
        return PlaybackEchoPolicy.isLikelyEcho(
            candidate,
            lastAssistantResponse,
            isEchoSuppressionWindowActive()
        );
    }

    private void markAssistantAudioStarted() {
        echoSuppressionUntilMs = Long.MAX_VALUE;
        confirmedBargeInUntilMs = 0L;
        clearPendingBargeInPartial();
    }

    private void markAssistantAudioEnded() {
        echoSuppressionUntilMs =
            SystemClock.elapsedRealtime()
                + (
                    usesPrivateAudioRoute()
                        ? 500L
                        : 1_800L
                );
        clearPendingBargeInPartial();
    }

    private void clearPendingBargeInPartial() {
        pendingBargeInPartial = "";
        pendingBargeInPartialAtMs = 0L;
    }

    private boolean partialBargeInIsConfirmed(
        String candidate
    ) {
        String normalised =
            PlaybackEchoPolicy.normalise(candidate);
        if (normalised.isEmpty()) return false;

        long now = SystemClock.elapsedRealtime();
        int wordCount = normalised.split(" ").length;
        if (usesPrivateAudioRoute() && wordCount >= 2) {
            confirmedBargeInUntilMs = now + 2_000L;
            clearPendingBargeInPartial();
            return true;
        }
        if (explicitSpeakerBargeIn(normalised)) {
            confirmedBargeInUntilMs = now + 2_000L;
            clearPendingBargeInPartial();
            return true;
        }
        boolean repeated =
            wordCount >= 2
                && normalised.equals(pendingBargeInPartial)
                && now - pendingBargeInPartialAtMs
                    <= 1_300L;

        pendingBargeInPartial = normalised;
        pendingBargeInPartialAtMs = now;

        if (repeated) {
            confirmedBargeInUntilMs = now + 2_000L;
            clearPendingBargeInPartial();
        }
        return repeated;
    }

    private void interruptCurrentTurnForBargeIn() {
        voiceFoundation.interrupting("user barge-in");
        if (!hasInterruptibleTurn()) return;

        JarvisRealtimeClient current = client;
        if (current != null) current.cancelResponse();

        realtimePlayback.interrupt();
        originalPlayback.stop();
        if (homeAssistantTts != null) homeAssistantTts.cancelActiveRun();
        if (speechFallback != null) speechFallback.cancel();

        brainActive = false;
        playbackActive = false;
        fallbackPending = false;
        fallbackSpeaking = false;
        turnShouldSpeak = false;
        turnReceivedRealtimeAudio = false;

        echoSuppressionUntilMs =
            SystemClock.elapsedRealtime() + 700L;
        clearPendingBargeInPartial();
        status("Interrupted — listening");
        broadcastState(true, true);
    }

    private void keepStandardBargeInArmed() {
        if (
            !voiceActive
                || stopping
                || endConversationAfterReply
                || !ConversationMode.STANDARD.equals(store.conversationMode())
                || standardSpeechEngine.isRunning()
        ) {
            return;
        }

        startStandardListening();
    }

    private void stopVoice(boolean stopService) {
        requestedVoiceActive = false;
        voiceActive = false;
        brainActive = false;
        wakeOwnedVoiceSession = false;
        followUpOwnerUntilMs = 0L;
        stopCaptureAndPlayback();
        turnShouldSpeak = false;
        turnReceivedRealtimeAudio = false;
        VoiceSessionState.setActive(false);
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
        if (!ready || stopping || voiceActive || !store.wakeEnabled()) return;
        voiceFoundation.offlineWake("dedicated wake armed");

        standardSpeechEngine.stop();
        audio.stop();
        releaseAudioFocus();

        if (!microphoneForegroundActive || !hasMicrophonePermission()) {
            wakePhraseEngine.stop();
            status(
                "Wake word paused — open Jarvis and allow microphone access"
            );
            broadcastState(false, false);
            return;
        }

        wakePhraseEngine.start(store.wakePhrase());
        status(
            "Wake word ready — say \"" + store.wakePhrase() + "\""
        );
        broadcastState(false, true);
    }

    private void stopCaptureAndPlayback() {
        echoSuppressionUntilMs = 0L;
        clearPendingBargeInPartial();
        wakePhraseEngine.stop();
        standardSpeechEngine.stop();
        audio.stop();
        if (speechFallback != null) speechFallback.cancel();
        fallbackPending = false;
        fallbackSpeaking = false;
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
        turnShouldSpeak = false;
        turnReceivedRealtimeAudio = false;
        VoiceSessionState.setActive(false);
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
        voiceFoundation.recovering(
            "connection lost: " + safe(reason, "unknown")
        );
        audio.stop();
        standardSpeechEngine.stop();
        status("Reconnecting: " + safe(reason, "connection lost"));
        broadcastState(voiceActive, false);
    }

    @Override public void onStatus(String message) {
        if (message != null && !message.isBlank()) status(message);
    }

    @Override public void onCoreEvent(
        RealtimeProtocol.Event event
    ) {
        if (event == null) return;

        if (
            event.conversationId != null
                && !event.conversationId.isBlank()
        ) {
            history.rekeyActiveConversation(
                event.conversationId
            );
            store.setConversationId(event.conversationId);
        }

        switch (event.type) {
            case "session.context" -> {
                String name = event.userName == null
                    || event.userName.isBlank()
                        ? store.userName()
                        : event.userName;
                String message = event.messageCount > 0
                    ? "Context restored for " + name
                        + " · " + event.messageCount + " messages"
                    : "Context ready for " + name;
                status(message);
                broadcastEvent(
                    "context",
                    "",
                    message,
                    voiceActive,
                    false
                );
            }
            case "tool.started" -> {
                String label = toolLabel(event.tool);
                status("Running " + label);
                broadcastEvent(
                    "tool",
                    "",
                    "Running " + label,
                    voiceActive,
                    false
                );
            }
            case "tool.completed" -> {
                String label = toolLabel(event.tool);
                String message =
                    event.message != null
                        && !event.message.isBlank()
                            ? event.message
                            : event.success
                                ? label + " completed"
                                : label + " failed";
                status(message);
                broadcastEvent(
                    "tool",
                    "",
                    message,
                    voiceActive,
                    false
                );
            }
            case "memory.context" -> {
                if (event.memoryUsed) {
                    broadcastEvent(
                        "memory",
                        "",
                        "Personal context used",
                        voiceActive,
                        false
                    );
                }
            }
            case "turn.summary" -> {
                // Summary is recorded in diagnostics. The spoken answer
                // remains the primary user-facing result.
            }
            default -> { }
        }
    }

    @Override public void onUserTranscript(String text) {
        if (text == null || text.isBlank()) return;
        if (ConversationEndPolicy.shouldEnd(text)) {
            endConversationAfterReply = true;
        }
        prepareSpokenTurn(true);
        brainActive = true;
        addMessage(ChatMessage.USER, text);
        status("Thinking");
    }

    @Override public void onAssistantTranscriptDelta(String text) {
        // The authoritative text stream is brain.delta. Realtime transcript is speech rendering only.
    }

    @Override public void onAssistantTranscriptDone(String text) {}

    @Override public void onAudio(byte[] pcm16) {
        if (!voiceActive || pcm16 == null || pcm16.length == 0) {
            return;
        }

        if (fallbackSpeaking) {
            return;
        }

        turnReceivedRealtimeAudio = true;
        fallbackPending = false;
        if (speechFallback != null) speechFallback.cancel();
        playbackActive = true;
        realtimePlayback.enqueue(pcm16);
    }

    @Override public void onSpeechStarted() {
        if (!voiceActive) return;
        if ((playbackActive || fallbackSpeaking) && !usesPrivateAudioRoute()) {
            return;
        }
        interruptCurrentTurnForBargeIn();
        prepareSpokenTurn(true);
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
        voiceFoundation.processing(
            ConversationMode.STANDARD.equals(
                store.conversationMode()
            ),
            "Jarvis Core processing"
        );
        if (voiceActive) turnShouldSpeak = true;
        turnReceivedRealtimeAudio = false;
        fallbackPending = false;
        fallbackSpeaking = false;
        if (speechFallback != null) speechFallback.cancel();
        broadcastEvent(
            "thinking",
            "",
            "",
            voiceActive,
            false
        );
        status("Thinking");
        if (
            voiceActive
                && ConversationMode.STANDARD.equals(store.conversationMode())
                && !endConversationAfterReply
        ) {
            main.postDelayed(this::keepStandardBargeInArmed, 180L);
        }
    }

    @Override public void onBrainDelta(String text) {
        if (text == null || text.isEmpty()) return;
        broadcastEvent("assistant_delta", ChatMessage.ASSISTANT, text, voiceActive, false);
    }

    @Override public void onBrainResponse(
        String text,
        boolean success,
        String conversationId
    ) {
        brainActive = false;
        String response = text == null ? "" : text.trim();
        lastAssistantResponse = response;

        if (!response.isEmpty()) {
            addMessage(ChatMessage.ASSISTANT, response);
        }

        status(
            success
                ? "Jarvis answered"
                : "Jarvis Core returned an error"
        );

        boolean useFallback =
            SpeechFallbackPolicy.shouldUseFallback(
                turnShouldSpeak,
                turnReceivedRealtimeAudio,
                success,
                response,
                VoiceCatalog.isOriginal(store.voiceId())
            );

        if (useFallback && speechFallback != null) {
            fallbackPending = true;
            speechFallback.schedule(response, 900L);
        }

        if (endConversationAfterReply) {
            main.postDelayed(
                () -> {
                    if (
                        endConversationAfterReply
                            && !brainActive
                            && !playbackActive
                            && !fallbackPending
                            && !fallbackSpeaking
                    ) {
                        finishConversation();
                    }
                },
                1_600L
            );
        } else if (
            !store.keepConversationOpen()
                && voiceActive
                && !playbackActive
                && !fallbackPending
                && !fallbackSpeaking
                && !VoiceCatalog.isOriginal(store.voiceId())
        ) {
            main.postDelayed(
                () -> {
                    if (
                        !playbackActive
                            && !fallbackPending
                            && !fallbackSpeaking
                    ) {
                        stopVoice(false);
                    }
                },
                350L
            );
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
        if (fallbackPending || fallbackSpeaking) return;
        if (endConversationAfterReply && !playbackActive) {
            finishConversation();
            return;
        }
        if (!voiceActive) return;
        if (!store.keepConversationOpen() && !playbackActive) {
            stopVoice(false);
        } else if (ConversationMode.STANDARD.equals(store.conversationMode()) &&
                   store.standardAutoListen() && !playbackActive && !brainActive) {
            main.postDelayed(this::startStandardListening, followUpListenDelayMs());
        }
    }

    @Override public void onError(String message) {
        brainActive = false;
        status("Error: " + safe(message, "unknown voice error"));
        broadcastEvent("error", ChatMessage.SYSTEM, safe(message, "Unknown voice error"), voiceActive, false);
    }

    @Override public void onAudioFrame(byte[] pcm16) {
        JarvisRealtimeClient current = client;

        if (
            playbackActive
                && !usesPrivateAudioRoute()
        ) {
            return;
        }

        if (
            voiceActive
                && ConversationMode.LIVE.equals(
                    store.conversationMode()
                )
                && current != null
        ) {
            current.sendAudio(pcm16);
        }
    }

    @Override public void onInputLevel(float level) {
        broadcastEvent("level", "", Float.toString(level), voiceActive, true);
    }

    @Override public void onAudioError(String message) {
        voiceFoundation.recovering(
            safe(message, "live audio error")
        );
        status(message);
    }

    @Override public void onPlaybackState(boolean playing) {
        if (!playing && fallbackSpeaking) return;
        playbackActive = playing;
        if (playing) {
            markAssistantAudioStarted();
            status("Jarvis is speaking — interrupt anytime");
            broadcastState(voiceActive, false);
            main.postDelayed(this::keepStandardBargeInArmed, bargeInArmDelayMs());
            return;
        }
        markAssistantAudioEnded();
        afterPlayback();
    }

    @Override public void onPlaybackStarted() {
        playbackActive = true;
        voiceFoundation.speaking(
            ConversationMode.STANDARD.equals(
                store.conversationMode()
            ),
            "assistant playback started"
        );
        markAssistantAudioStarted();
        status("Jarvis is speaking — interrupt anytime");
        broadcastState(voiceActive, false);
        main.postDelayed(this::keepStandardBargeInArmed, bargeInArmDelayMs());
    }

    @Override public void onPlaybackCompleted() {
        playbackActive = false;
        markAssistantAudioEnded();
        afterPlayback();
    }

    @Override public void onPlaybackError(String message) {
        playbackActive = false;
        markAssistantAudioEnded();
        status("Audio error: " + safe(message, "playback failed"));
        afterPlayback();
    }

    private void afterPlayback() {
        fallbackPending = false;
        fallbackSpeaking = false;
        turnShouldSpeak = false;
        turnReceivedRealtimeAudio = false;

        if (endConversationAfterReply) {
            finishConversation();
            return;
        }
        if (!voiceActive) return;

        if (wakeOwnedVoiceSession) {
            followUpOwnerUntilMs =
                SystemClock.elapsedRealtime()
                    + 9_000L;
        }
        if (!store.keepConversationOpen()) {
            stopVoice(false);
        } else if (ConversationMode.STANDARD.equals(store.conversationMode()) && store.standardAutoListen()) {
            main.postDelayed(this::startStandardListening, followUpListenDelayMs());
        } else {
            status("Live voice — listening continuously");
            broadcastState(true, true);
        }
    }

    @Override public void onFallbackStarted() {
        if (stopping || !voiceActive) {
            if (speechFallback != null) speechFallback.cancel();
            fallbackPending = false;
            fallbackSpeaking = false;
            return;
        }

        fallbackPending = false;
        fallbackSpeaking = true;
        playbackActive = true;
        markAssistantAudioStarted();
        realtimePlayback.interrupt();
        status("Jarvis is speaking — interrupt anytime");
        broadcastState(true, false);
        main.postDelayed(
            this::keepStandardBargeInArmed,
            bargeInArmDelayMs()
        );
    }

    @Override public void onFallbackDone() {
        if (!fallbackSpeaking && !playbackActive) return;
        fallbackPending = false;
        fallbackSpeaking = false;
        playbackActive = false;
        markAssistantAudioEnded();
        afterPlayback();
    }

    @Override public void onFallbackError(String message) {
        fallbackPending = false;
        fallbackSpeaking = false;
        playbackActive = false;
        markAssistantAudioEnded();
        status(
            "Speech fallback unavailable: "
                + safe(message, "Android speech failed")
        );
        if (voiceActive) afterPlayback();
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

    @Override public void onWakePhrase(
        String transcript,
        String command
    ) {
        if (stopping) return;

        String verifiedCommand =
            command == null ? "" : command.trim();

        if (verifiedCommand.isEmpty()) {
            requestedVoiceActive = false;
            if (ready && !voiceActive) {
                armWakeWord();
            }
            return;
        }

        if (
            store.assistantOverlayEnabled()
                && JarvisVoiceInteractionService
                    .showOverlayIfActive(
                        this,
                        verifiedCommand,
                        "wake_word"
                    )
        ) {
            return;
        }

        wakeOwnedVoiceSession = true;
        followUpOwnerUntilMs =
            SystemClock.elapsedRealtime() + 30_000L;
        requestedVoiceActive = true;
        beginVoice();
        queueOrSend(verifiedCommand, true);
    }

    @Override public void onWakeStatus(String message) {
        if (!voiceActive && !stopping) status(message);
    }

    @Override public void onWakeError(String message) {
        if (!voiceActive && !stopping) status(message);
    }

    @Override public void onStandardReady() {
        voiceFoundation.listeningStandard(
            "standard recogniser ready"
        );
        status("Listening");
        broadcastState(true, true);
    }

    @Override public void onStandardPartial(
        String text
    ) {
        if (text == null || text.isBlank()) {
            return;
        }

        if (isLikelyPlaybackEcho(text)) {
            return;
        }

        if (hasInterruptibleTurn()) {
            boolean outputActive =
                playbackActive || fallbackSpeaking;

            if (
                !outputActive
                    && wakeOwnedVoiceSession
                    && !FollowUpVoicePolicy
                        .acceptFollowUp(
                            text,
                            standardSpeechEngine
                                .lastConfidence(),
                            true,
                            usesPrivateAudioRoute()
                        )
            ) {
                return;
            }

            if (
                !outputActive
                    || partialBargeInIsConfirmed(text)
            ) {
                interruptCurrentTurnForBargeIn();
            } else {
                return;
            }
        }

        broadcastEvent(
            "draft",
            ChatMessage.USER,
            text,
            true,
            true
        );
    }


    @Override public void onStandardFinal(
        String text
    ) {
        if (!voiceActive) return;

        String heard =
            text == null ? "" : text.trim();

        if (heard.isEmpty()) {
            main.postDelayed(
                this::keepStandardBargeInArmed,
                300L
            );
            return;
        }

        boolean explicitWake =
            FollowUpVoicePolicy.hasExplicitWake(
                heard
            );
        String command =
            FollowUpVoicePolicy.stripWakePrefix(
                heard
            );

        if (command.isEmpty()) {
            main.postDelayed(
                this::keepStandardBargeInArmed,
                250L
            );
            return;
        }

        boolean outputActive =
            playbackActive || fallbackSpeaking;
        boolean confirmed =
            SystemClock.elapsedRealtime()
                < confirmedBargeInUntilMs
                || explicitWake;

        if (
            outputActive
                && !usesPrivateAudioRoute()
                && !confirmed
                && !explicitSpeakerBargeIn(heard)
        ) {
            clearPendingBargeInPartial();
            main.postDelayed(
                this::keepStandardBargeInArmed,
                300L
            );
            return;
        }

        if (isLikelyPlaybackEcho(command)) {
            clearPendingBargeInPartial();
            main.postDelayed(
                this::keepStandardBargeInArmed,
                250L
            );
            return;
        }

        boolean withinOwnerWindow =
            followUpOwnerUntilMs <= 0L
                || SystemClock.elapsedRealtime()
                    <= followUpOwnerUntilMs;

        if (
            !outputActive
                && wakeOwnedVoiceSession
                && !FollowUpVoicePolicy
                    .acceptFollowUp(
                        heard,
                        standardSpeechEngine
                            .lastConfidence(),
                        withinOwnerWindow,
                        usesPrivateAudioRoute()
                    )
        ) {
            status("Background speech ignored");
            main.postDelayed(
                this::keepStandardBargeInArmed,
                250L
            );
            return;
        }

        followUpOwnerUntilMs =
            SystemClock.elapsedRealtime() + 30_000L;
        clearPendingBargeInPartial();
        interruptCurrentTurnForBargeIn();
        queueOrSend(command, true);
        broadcastState(true, false);

        if (!endConversationAfterReply) {
            main.postDelayed(
                this::keepStandardBargeInArmed,
                200L
            );
        }
    }


    @Override public void onStandardError(String message) {
        status(message);
        broadcastState(voiceActive, false);

        if (
            wakeOwnedVoiceSession
                && followUpOwnerUntilMs > 0L
                && SystemClock.elapsedRealtime()
                    > followUpOwnerUntilMs
        ) {
            stopVoice(false);
            return;
        }

        if (
            voiceActive
                && !endConversationAfterReply
                && !stopping
                && (
                    hasInterruptibleTurn()
                        || store.keepConversationOpen()
                )
        ) {
            main.postDelayed(this::keepStandardBargeInArmed, 600L);
        }
    }


    private void finishConversation() {
        voiceFoundation.closing(
            "conversation closing phrase"
        );
        endConversationAfterReply = false;
        store.resetToDedicatedWake();
        stopVoice(false);
        broadcastEvent("conversation.ended", "", "", false, false);
        main.postDelayed(
            () -> {
                if (ready && !voiceActive && store.wakeEnabled()) {
                    armWakeWord();
                }
            },
            300L
        );
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
            .setSmallIcon(R.drawable.ic_jarvis_status)
            .setContentTitle("Jarvis")
            .setLargeIcon(
                BitmapFactory.decodeResource(
                    getResources(),
                    R.drawable.jarvis_logo_ui
                )
            )
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
        wakeLock.acquire();
    }

    private void releaseWakeLock() {
        if (wakeLock != null && wakeLock.isHeld()) {
            try { wakeLock.release(); } catch (Exception ignored) {}
        }
        wakeLock = null;
    }

    @Override public void onDestroy() {
        if (alpha6AudioRouteMonitor != null) {
            alpha6AudioRouteMonitor.close();
            alpha6AudioRouteMonitor = null;
        }
        new VoiceDiagnosticsStore(this).recordLifecycle(
            "Service stopped",
            true
        );
        stopping = true;
        VoiceSessionState.setActive(false);
        closeClientAndAudio();
        if (speechFallback != null) {
            speechFallback.shutdown();
            speechFallback = null;
        }
        releaseAudioFocus();
        releaseWakeLock();
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent intent) {
        return null;
    }

    private static String toolLabel(String raw) {
        String value = raw == null ? "" : raw.trim();
        if (value.isEmpty()) return "Home Assistant action";
        value = value.replace('_', ' ');
        return Character.toUpperCase(value.charAt(0))
            + value.substring(1);
    }

    private static String shorten(String value, int max) {
        String text = value == null ? "" : value.trim();
        if (text.length() <= max) return text;
        return text.substring(0, Math.max(1, max - 1)) + "…";
    }

    private static String safe(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }

    @Override public void onTaskRemoved(Intent rootIntent) {
        new VoiceDiagnosticsStore(this).recordLifecycle(
            "Task removed — session recoverable",
            true
        );
        super.onTaskRemoved(rootIntent);
    }

}
