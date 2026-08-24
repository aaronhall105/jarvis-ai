package com.aaron.jarvisvoice;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;

import java.io.IOException;
import java.util.concurrent.TimeUnit;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;
import okio.ByteString;

public final class JarvisRealtimeClient {
    public interface Listener {
        void onConnected();
        void onReady(String model, String voice, String voiceMode, String conversationMode, boolean unifiedBrain);
        void onDisconnected(String reason);
        void onStatus(String message);
        void onUserTranscript(String text);
        void onAssistantTranscriptDelta(String text);
        void onAssistantTranscriptDone(String text);
        void onAudio(byte[] pcm16);
        void onSpeechStarted();
        void onAudioDone();
        void onBrainStarted(String command);
        void onBrainDelta(String text);
        void onBrainResponse(
            String text,
            boolean success,
            String conversationId
        );

        default boolean onBrainResponseDurably(
            long clientTurnId,
            String text,
            boolean success,
            String conversationId
        ) {
            onBrainResponse(
                text,
                success,
                conversationId
            );

            return true;
        }
        void onOriginalTts(String text);
        void onTurnDone();
        void onCoreEvent(RealtimeProtocol.Event event);
        void onError(String message);
    }

    private static final long AUTH_TIMEOUT_MS = 12_000L;
    private static final long READY_TIMEOUT_MS = 20_000L;
    private static final long PING_INTERVAL_MS = 10_000L;
    private static final long LAN_RECHECK_MS = 30_000L;
    private static final long TURN_RECOVERY_RECHECK_MS = 750L;
    private static final int MAX_TURN_RECOVERY_STATUS_CHECKS = 12;

    private final String lanCoreUrl;
    private final CoreEndpointSelector endpoints;
    private final String token;
    private final String deviceId;
    private final String userId;
    private final String userName;
    private final String voice;
    private final String voiceMode;
    private final String conversationMode;
    private final String vadEagerness;
    private final String conversationId;
    private final String endpoint;
    private final Listener listener;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final VoiceDiagnosticsStore diagnostics;
    private final TurnPerformanceTracker performance;
    private final NetworkQualityMonitor network;
    private final OkHttpClient http = new OkHttpClient.Builder()
        .pingInterval(12, TimeUnit.SECONDS)
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build();

    private WebSocket socket;
    private boolean authenticated;
    private boolean ready;
    private boolean opening;
    private boolean probing;
    private boolean shouldReconnect;
    private int reconnectAttempt;
    private boolean reconnectScheduled;
    private int reconnectScheduleGeneration;
    private int generation;
    private long openStartedAtMs;
    private long pingStartedAtMs;
    private long turnStartedAtMs;
    private boolean firstAudioMeasured;
    private final ClientTurnIdStore turnIds;
    private final TurnRecoveryJournal recoveryJournal;
    private final TurnRecoveryState turnRecovery =
        new TurnRecoveryState();

    private TurnRecoveryJournal.Snapshot recoverySnapshot;
    private boolean recoveryJournalReadable = true;

    private long activeClientTurnId;
    private RealtimeProtocol.Event deferredReadyEvent;
    private int recoveryScheduleGeneration;
    private int highestServerGeneration;
    private int minimumServerGeneration;
    private int audioServerGeneration;
    private String activeCoreUrl;
    private String activeEndpointName = "LAN";

    public JarvisRealtimeClient(
        Context context,
        String coreUrl,
        String token,
        String deviceId,
        String userId,
        String userName,
        String voice,
        String voiceMode,
        String conversationMode,
        String vadEagerness,
        String conversationId,
        String endpoint,
        Listener listener
    ) {
        this.lanCoreUrl = coreUrl;
        this.activeCoreUrl = coreUrl;
        this.token = token;
        this.deviceId = deviceId;
        this.userId = userId;
        this.userName = userName;
        this.voice = voice;
        this.voiceMode = voiceMode;
        this.conversationMode = ConversationMode.normalise(conversationMode);
        this.vadEagerness = vadEagerness;
        this.conversationId = conversationId;
        this.endpoint = "WATCH".equalsIgnoreCase(endpoint) ? "WATCH" : "PHONE";
        this.listener = listener;

        turnIds =
            new ClientTurnIdStore(context);

        diagnostics =
            new VoiceDiagnosticsStore(context);

        performance =
            new TurnPerformanceTracker(diagnostics);

        recoveryJournal =
            new TurnRecoveryJournal(
                context,
                deviceId,
                conversationId,
                this.endpoint
            );

        restoreDurableRecovery();

        endpoints =
            new CoreEndpointSelector(
                context,
                coreUrl
            );
        lanRecheckTask = () -> {
            if (!ready || endpoints.isLan(activeCoreUrl)) return;
            endpoints.probeLan(new CoreEndpointSelector.Listener() {
                @Override public void onSelected(String url, String name) {
                    if (!ready || endpoints.isLan(activeCoreUrl)) return;
                    returnToLan(url, name);
                }

                @Override public void onUnavailable(String reason) {
                    if (ready && !endpoints.isLan(activeCoreUrl)) {
                        scheduleLanRecheck();
                    }
                }
            });
        };
        network = new NetworkQualityMonitor(
            context,
            new NetworkQualityMonitor.Listener() {
                @Override public void onNetworkAvailable() {
                    main.post(JarvisRealtimeClient.this::networkAvailable);
                }

                @Override public void onNetworkLost() {
                    main.post(JarvisRealtimeClient.this::networkLost);
                }
            }
        );
    }

    public void connect() {
        shouldReconnect = true;
        reconnectAttempt = 0;
        reconnectScheduled = false;
        reconnectScheduleGeneration++;
        if (!network.isAvailable()) {
            diagnostics.recordNetworkStatus(false, "Waiting for network");
            diagnostics.recordCoreReachability("Unavailable", "No network");
            post(() -> listener.onStatus("Offline — waiting for network"));
            return;
        }
        diagnostics.recordNetworkStatus(true, "Online");
        open();
    }

    public void close() {
        closeInternal(
            true
        );
    }

    void closeForReplacement() {
        closeInternal(
            false
        );
    }

    private void closeInternal(
        boolean abandonRecovery
    ) {
        shouldReconnect = false;
        reconnectScheduled = false;
        reconnectScheduleGeneration++;

        performance.abandonTurn();

        if (
            abandonRecovery
                && turnRecovery.hasPending()
        ) {
            durablyAbandonRecovery(
                turnRecovery.clientTurnId()
            );
        }

        /*
         * The Java object may now disappear. A replacement
         * client reconstructs this logical turn from the
         * journal when abandonRecovery is false.
         */
        turnRecovery.clear();
        activeClientTurnId = 0L;
        deferredReadyEvent = null;
        recoveryScheduleGeneration++;

        highestServerGeneration = 0;
        minimumServerGeneration = 0;
        audioServerGeneration = 0;

        authenticated = false;
        ready = false;
        opening = false;
        probing = false;

        generation++;

        endpoints.cancel();
        cancelTimers();

        WebSocket current = socket;
        socket = null;

        if (current != null) {
            current.send(
                RealtimeProtocol.stop()
            );

            current.close(
                1000,
                "Jarvis voice stopped"
            );
        }

        network.close();
    }

    public boolean sendAudio(byte[] pcm16) {
        WebSocket current = socket;
        if (!ready || current == null || pcm16 == null || pcm16.length == 0) return false;
        if (current.queueSize() > 384_000L) {
            performance.recordDroppedAudioFrame();
            return false;
        }
        return current.send(ByteString.of(pcm16, 0, pcm16.length));
    }

    public void cancelResponse() {
        WebSocket current = socket;

        long cancelledTurnId =
            activeClientTurnId;

        if (
            cancelledTurnId > 0L
                && turnRecovery.matches(
                    cancelledTurnId
                )
        ) {
            if (
                !durablyAbandonRecovery(
                    cancelledTurnId
                )
            ) {
                post(() -> listener.onError(
                    "Jarvis could not persist cancellation "
                        + "safely"
                ));
            }
        }

        audioServerGeneration = 0;

        minimumServerGeneration =
            Math.max(
                minimumServerGeneration,
                highestServerGeneration + 1
            );

        if (
            authenticated
                && current != null
                && cancelledTurnId > 0L
        ) {
            performance.abandonTurn();

            current.send(
                RealtimeProtocol.cancel(
                    cancelledTurnId
                )
            );
        }
    }

    public boolean sendText(
        String text,
        boolean speak
    ) {
        WebSocket current = socket;

        if (
            !ready
                || current == null
                || text == null
                || text.isBlank()
        ) {
            return false;
        }

        /*
         * Never overwrite recovery authority for a previous
         * logical turn.
         */
        if (turnRecovery.hasPending()) {
            post(() -> listener.onStatus(
                "Finishing previous Jarvis request"
            ));

            requestTurnRecovery(
                generation
            );

            return false;
        }

        if (
            !recoveryJournalReadable
                && !restoreDurableRecovery()
        ) {
            post(() -> listener.onError(
                "Jarvis recovery storage is unavailable"
            ));

            return false;
        }

        if (turnRecovery.hasPending()) {
            requestTurnRecovery(
                generation
            );

            return false;
        }

        String command =
            text.trim();

        final long clientTurnId;

        try {
            clientTurnId =
                turnIds.next();

        } catch (Exception exception) {
            post(() -> listener.onError(
                "Could not allocate Jarvis request id: "
                    + safeMessage(exception)
            ));

            return false;
        }

        TurnRecoveryJournal.Snapshot snapshot =
            new TurnRecoveryJournal.Snapshot(
                clientTurnId,
                command,
                speak,
                deviceId,
                conversationId,
                endpoint,
                false,
                false,
                System.currentTimeMillis()
            );

        /*
         * CRITICAL ORDER:
         *
         *   durable journal
         *       BEFORE
         *   WebSocket transmission
         */
        if (
            !persistNewRecoverySnapshot(
                snapshot
            )
        ) {
            return false;
        }

        turnRecovery.begin(
            clientTurnId,
            command,
            speak
        );

        activeClientTurnId =
            clientTurnId;

        audioServerGeneration = 0;

        turnStartedAtMs =
            SystemClock.elapsedRealtime();

        firstAudioMeasured = false;
        performance.beginTurn();

        try {
            boolean queued = current.send(
                RealtimeProtocol.text(
                    command,
                    speak,
                    clientTurnId
                )
            );

            if (!queued) {
                diagnostics.recordRecovery(
                    "Realtime text queue rejected; "
                        + "reconciling turn="
                        + clientTurnId
                );

                performance.abandonTurn();

                failCurrent(
                    generation,
                    "Jarvis request transport changed"
                );
            }

            /*
             * Once the durable record exists, this logical
             * request belongs to JarvisRealtimeClient even when
             * OkHttp could not prove transmission.
             */
            return true;

        } catch (Exception exception) {
            diagnostics.recordRecovery(
                "Realtime text send became ambiguous "
                    + "turn="
                    + clientTurnId
                    + " · "
                    + safeMessage(exception)
            );

            performance.abandonTurn();

            failCurrent(
                generation,
                "Jarvis request transport changed"
            );

            return true;
        }
    }

    public boolean isAuthenticated() {
        return authenticated;
    }

    private void open() {
        if (!shouldReconnect || opening || authenticated || probing) return;
        if (!network.isAvailable()) {
            diagnostics.recordNetworkStatus(false, "Reconnect paused");
            diagnostics.recordCoreReachability("Unavailable", "No network");
            return;
        }

        final int probeGeneration = ++generation;
        probing = true;
        opening = true;
        ready = false;
        diagnostics.recordNetworkStatus(true, "Online");
        diagnostics.recordCoreReachability(
            "Checking",
            "Probing LAN and Tailscale"
        );
        post(() -> listener.onStatus("Finding Jarvis Core"));

        endpoints.select(new CoreEndpointSelector.Listener() {
            @Override public void onSelected(String url, String name) {
                if (
                    probeGeneration != generation
                        || !shouldReconnect
                        || !network.isAvailable()
                ) {
                    return;
                }
                probing = false;
                opening = false;
                activeCoreUrl = url;
                activeEndpointName = name;
                diagnostics.recordEndpoint(name, url);
                diagnostics.recordCoreReachability(
                    "Reachable",
                    name + " health check passed"
                );
                openSocket(url, name);
            }

            @Override public void onUnavailable(String reason) {
                if (probeGeneration != generation || !shouldReconnect) return;
                probing = false;
                opening = false;
                ready = false;
                diagnostics.recordCoreReachability("Unreachable", reason);
                post(() -> listener.onDisconnected("Jarvis Core unreachable"));
                scheduleReconnect("Jarvis Core unreachable: " + reason);
            }
        });
    }

    private void openSocket(String selectedCoreUrl, String endpointName) {
        if (!shouldReconnect || !network.isAvailable()) return;
        final int currentGeneration = ++generation;
        final String websocketUrl;
        try {
            websocketUrl = CoreUrl.websocket(selectedCoreUrl);
        } catch (Exception exception) {
            diagnostics.recordCoreReachability(
                "Unreachable",
                safeMessage(exception)
            );
            post(() -> listener.onError(safeMessage(exception)));
            scheduleReconnect(safeMessage(exception));
            return;
        }

        opening = true;
        probing = false;
        ready = false;
        activeCoreUrl = selectedCoreUrl;
        activeEndpointName = endpointName;
        openStartedAtMs = SystemClock.elapsedRealtime();
        diagnostics.recordNetworkStatus(true, "Online");
        diagnostics.recordEndpoint(endpointName, selectedCoreUrl);
        post(() -> listener.onStatus(
            "Connecting to Jarvis Core via " + endpointName
        ));

        Request request = new Request.Builder().url(websocketUrl).build();
        socket = http.newWebSocket(request, new WebSocketListener() {
            @Override public void onOpen(WebSocket webSocket, Response response) {
                if (currentGeneration != generation) return;
                opening = false;
                diagnostics.recordConnectionLatency(
                    SystemClock.elapsedRealtime() - openStartedAtMs
                );
                diagnostics.recordCoreReachability(
                    "Reachable",
                    endpointName + " WebSocket connected"
                );
                try {
                    webSocket.send(RealtimeProtocol.auth(
                        token,
                        deviceId,
                        userId,
                        userName,
                        voice,
                        voiceMode,
                        conversationMode,
                        vadEagerness,
                        conversationId,
                        endpoint
                    ));
                    scheduleAuthTimeout(currentGeneration);
                } catch (Exception exception) {
                    failCurrent(
                        currentGeneration,
                        "Could not authenticate: " + safeMessage(exception)
                    );
                }
            }

            @Override public void onMessage(WebSocket webSocket, String text) {
                if (currentGeneration != generation) return;
                handleText(text, currentGeneration);
            }

            @Override public void onMessage(WebSocket webSocket, ByteString bytes) {
                if (currentGeneration != generation) return;
                if (!RealtimeAudioOwnership.accepts(
                        audioServerGeneration,
                        minimumServerGeneration
                )) {
                    diagnostics.recordRecovery(
                        "Discarded unowned realtime audio after cancellation"
                    );
                    return;
                }
                performance.markFirstAudio();
                if (!firstAudioMeasured && turnStartedAtMs > 0L) {
                    firstAudioMeasured = true;
                    diagnostics.recordFirstAudioLatency(
                        SystemClock.elapsedRealtime() - turnStartedAtMs
                    );
                }
                byte[] audio = bytes.toByteArray();
                post(() -> listener.onAudio(audio));
            }

            @Override public void onClosed(
                WebSocket webSocket,
                int code,
                String reason
            ) {
                if (currentGeneration != generation) return;
                opening = false;
                authenticated = false;
                ready = false;
                performance.abandonTurn();
                turnRecovery.onTransportLost();
                cancelTimers();
                String value = reason == null || reason.isBlank()
                    ? "Connection closed"
                    : reason;
                diagnostics.recordCoreReachability("Unreachable", value);
                post(() -> listener.onDisconnected(value));
                scheduleReconnect(value);
            }

            @Override public void onFailure(
                WebSocket webSocket,
                Throwable throwable,
                Response response
            ) {
                if (currentGeneration != generation) return;
                opening = false;
                authenticated = false;
                ready = false;
                performance.abandonTurn();
                turnRecovery.onTransportLost();
                cancelTimers();
                String reason = throwable == null
                    ? "Connection failed"
                    : throwable.getMessage();
                String value = reason == null ? "Connection failed" : reason;
                diagnostics.recordCoreReachability("Unreachable", value);
                post(() -> listener.onDisconnected(value));
                scheduleReconnect(value);
            }
        });
    }

    private void handleText(String raw, int currentGeneration) {
        final RealtimeProtocol.Event event;
        try {
            event = RealtimeProtocol.parse(raw);
        } catch (Exception exception) {
            post(() -> listener.onError("Invalid Jarvis Core message: " + safeMessage(exception)));
            return;
        }

        if (isStaleTurnEvent(event)) {
            diagnostics.recordRecovery(
                "Discarded stale realtime event " + event.type
                    + " turn=" + event.clientTurnId
            );
            return;
        }

        switch (event.type) {
            case "auth.ok" -> {
                authenticated = true;
                reconnectAttempt = 0;
                // A newly authenticated Core WebSocket is a new server-session
                // epoch. Old socket callbacks are already fenced by
                // currentGeneration; reset only the server generation floor so
                // the new session's first legitimate turn is accepted.
                highestServerGeneration = 0;
                minimumServerGeneration = 0;
                audioServerGeneration = 0;
                main.removeCallbacks(authTimeout);
                scheduleReadyTimeout(currentGeneration);
                post(listener::onConnected);
            }
            case "auth.error" -> {
                authenticated = false;
                ready = false;
                shouldReconnect = false;
                cancelTimers();
                post(() -> listener.onError(
                    event.message.isBlank()
                        ? "Mobile voice token rejected"
                        : event.message
                ));
            }
            case "ready" -> {
                ready = true;
                reconnectAttempt = 0;
                reconnectScheduled = false;
                reconnectScheduleGeneration++;
                main.removeCallbacks(readyTimeout);

                diagnostics.recordNetworkStatus(
                    true,
                    "Online"
                );

                diagnostics.recordCoreReachability(
                    "Reachable",
                    event.transport + " · ready"
                );

                diagnostics.recordEndpoint(
                    activeEndpointName,
                    activeCoreUrl
                );

                schedulePing();
                scheduleLanRecheck();

                deferredReadyEvent = event;

                if (turnRecovery.hasPending()) {
                    turnRecovery.resetStatusChecks();

                    diagnostics.recordRecovery(
                        "Reconciling realtime turn="
                            + turnRecovery.clientTurnId()
                    );

                    post(() -> listener.onStatus(
                        "Checking previous Jarvis request"
                    ));

                    requestTurnRecovery(
                        currentGeneration
                    );
                } else {
                    publishDeferredReady();
                }
            }
            case "pong" -> {
                if (pingStartedAtMs > 0L) {
                    diagnostics.recordRoundTrip(
                        SystemClock.elapsedRealtime() - pingStartedAtMs
                    );
                }
                schedulePing();
            }
            case "status" -> post(() -> listener.onStatus(event.message));
            case "speech.started" -> {
                performance.beginTurn();
                post(listener::onSpeechStarted);
            }
            case "user.transcript" -> {
                turnStartedAtMs = SystemClock.elapsedRealtime();
                firstAudioMeasured = false;
                post(() -> listener.onUserTranscript(event.text));
            }
            case "assistant.transcript.delta" -> {
                performance.markFirstToken();
                post(() -> listener.onAssistantTranscriptDelta(event.text));
            }
            case "assistant.transcript.done" -> post(() -> listener.onAssistantTranscriptDone(event.text));
            case "audio.done" -> post(listener::onAudioDone);
            case "brain.started" -> {
                audioServerGeneration = event.generation;
                performance.markBrainStarted();
                if (turnStartedAtMs <= 0L) {
                    turnStartedAtMs = SystemClock.elapsedRealtime();
                }
                firstAudioMeasured = false;
                post(() -> listener.onBrainStarted(
                    event.command.isBlank() ? event.text : event.command
                ));
            }
            case "brain.delta" -> {
                performance.markFirstToken();
                post(() -> listener.onBrainDelta(event.text));
            }
            case "brain.response" -> {
                long clientTurnId =
                    event.clientTurnId;

                if (
                    clientTurnId > 0L
                        && turnRecovery.matches(
                            clientTurnId
                        )
                ) {
                    post(() -> {
                        boolean persisted;

                        try {
                            persisted =
                                listener
                                    .onBrainResponseDurably(
                                        clientTurnId,
                                        event.text,
                                        event.success,
                                        event.conversationId
                                    );

                        } catch (
                            Exception exception
                        ) {
                            persisted = false;

                            listener.onError(
                                "Could not persist Jarvis "
                                    + "response: "
                                    + safeMessage(
                                        exception
                                    )
                            );
                        }

                        if (
                            !turnRecovery.matches(
                                clientTurnId
                            )
                        ) {
                            return;
                        }

                        if (!persisted) {
                            diagnostics.recordRecovery(
                                "Assistant response not yet "
                                    + "durable turn="
                                    + clientTurnId
                            );

                            listener.onError(
                                "Jarvis could not save the "
                                    + "response safely"
                            );

                            return;
                        }

                        if (
                            markDurableResponseDelivered(
                                clientTurnId
                            )
                        ) {
                            turnRecovery
                                .markResponseDelivered(
                                    clientTurnId
                                );

                        } else {
                            diagnostics.recordRecovery(
                                "Assistant response saved but "
                                    + "delivery checkpoint "
                                    + "failed turn="
                                    + clientTurnId
                            );

                            listener.onStatus(
                                "Confirming saved Jarvis "
                                    + "response"
                            );
                        }
                    });

                } else {
                    post(() ->
                        listener.onBrainResponse(
                            event.text,
                            event.success,
                            event.conversationId
                        )
                    );
                }
            }
            case "original.tts" ->
                post(() -> listener.onOriginalTts(event.text));

            case "turn.accepted" -> {
                if (
                    turnRecovery.matches(
                        event.clientTurnId
                    )
                ) {
                    diagnostics.recordRecovery(
                        "Core accepted realtime turn="
                            + event.clientTurnId
                    );
                }
            }

            case "turn.status" ->
                handleTurnRecoveryStatus(
                    event,
                    currentGeneration,
                    false
                );

            case "turn.conflict" ->
                handleTurnRecoveryStatus(
                    event,
                    currentGeneration,
                    true
                );

            case "turn.interrupted" ->
                handleDirectTerminalRecovery(
                    event,
                    "interrupted"
                );

            case "turn.cancelled" ->
                handleDirectTerminalRecovery(
                    event,
                    "cancelled"
                );

            case "turn.done" -> {
                performance.finishTurn();

                long clientTurnId =
                    event.clientTurnId;

                boolean tracked =
                    clientTurnId > 0L
                        && clientTurnId
                            == activeClientTurnId
                        && turnRecovery.matches(
                            clientTurnId
                        );

                if (event.generation > 0) {
                    minimumServerGeneration =
                        Math.max(
                            minimumServerGeneration,
                            event.generation + 1
                        );
                }

                if (tracked) {
                    post(() ->
                        finishTrackedTurnDone(
                            clientTurnId
                        )
                    );

                } else {
                    post(listener::onTurnDone);
                }
            }
            case "session.context",
                 "tool.started",
                 "tool.completed",
                 "memory.context",
                 "turn.summary" -> {
                if ("session.context".equals(event.type)) {
                    diagnostics.recordCoreContext(
                        event.userName,
                        event.conversationId,
                        event.messageCount
                    );
                } else if (
                    "tool.started".equals(event.type)
                        || "tool.completed".equals(event.type)
                ) {
                    diagnostics.recordToolEvent(
                        event.tool,
                        event.success
                    );
                } else if ("memory.context".equals(event.type)) {
                    diagnostics.recordMemoryContext(
                        event.memoryUsed,
                        event.messageCount
                    );
                }
                post(() -> listener.onCoreEvent(event));
            }
            case "error" -> post(() -> listener.onError(
                event.message.isBlank()
                    ? "Jarvis voice error"
                    : event.message
            ));
            default -> { }
        }
    }

    private void publishDeferredReady() {
        RealtimeProtocol.Event event =
            deferredReadyEvent;

        if (event == null) return;

        deferredReadyEvent = null;

        post(() -> listener.onReady(
            event.model,
            event.voice,
            event.voiceMode,
            event.conversationMode,
            event.unifiedBrain
        ));
    }

    private void requestTurnRecovery(
        int socketGeneration
    ) {
        if (
            socketGeneration != generation
                || !ready
                || !turnRecovery.hasPending()
        ) {
            return;
        }

        int check =
            turnRecovery.noteStatusCheck();

        if (
            check
                > MAX_TURN_RECOVERY_STATUS_CHECKS
        ) {
            abandonRecoveryWithoutReplay(
                "Jarvis could not confirm the "
                    + "previous request safely"
            );
            return;
        }

        WebSocket current = socket;

        if (current == null) return;

        try {
            long clientTurnId =
                turnRecovery.clientTurnId();

            boolean queued = current.send(
                RealtimeProtocol.turnStatus(
                    clientTurnId
                )
            );

            if (!queued) {
                diagnostics.recordRecovery(
                    "Turn status queue rejected "
                        + "turn="
                        + clientTurnId
                );

                failCurrent(
                    socketGeneration,
                    "Jarvis recovery transport changed"
                );
            }

        } catch (Exception exception) {
            post(() -> listener.onError(
                "Could not check previous request: "
                    + safeMessage(exception)
            ));

            scheduleTurnRecoveryRecheck(
                socketGeneration
            );
        }
    }

    private void scheduleTurnRecoveryRecheck(
        int socketGeneration
    ) {
        int scheduleGeneration =
            ++recoveryScheduleGeneration;

        main.postDelayed(
            () -> {
                if (
                    scheduleGeneration
                            != recoveryScheduleGeneration
                        || socketGeneration
                            != generation
                        || !ready
                        || !turnRecovery.hasPending()
                ) {
                    return;
                }

                requestTurnRecovery(
                    socketGeneration
                );
            },
            TURN_RECOVERY_RECHECK_MS
        );
    }

    private void handleTurnRecoveryStatus(
        RealtimeProtocol.Event event,
        int socketGeneration,
        boolean conflict
    ) {
        TurnRecoveryPolicy.Action action =
            turnRecovery.action(
                event.clientTurnId,
                event.found,
                event.turnStatus,
                conflict
            );

        switch (action) {
            case REPLAY_UNKNOWN -> {
                if (
                    mayReplayUnknown()
                ) {
                    replayRecoveredTurn(
                        socketGeneration
                    );
                } else {
                    finishUnknownWithoutReplay();
                }
            }

            case WAIT_ACCEPTED -> {
                diagnostics.recordRecovery(
                    "Waiting for Core terminal state "
                        + "turn="
                        + event.clientTurnId
                );

                post(() -> listener.onStatus(
                    "Finishing previous Jarvis request"
                ));

                scheduleTurnRecoveryRecheck(
                    socketGeneration
                );
            }

            case RESTORE_COMPLETED ->
                restoreCompletedTurn(
                    event
                );

            case FINISH_CANCELLED ->
                finishRecoveredTerminal(
                    "Previous Jarvis request was cancelled"
                );

            case FINISH_INTERRUPTED ->
                finishRecoveredTerminal(
                    "Previous Jarvis request was interrupted"
                );

            case FAIL_CONFLICT ->
                failRecoveredConflict(
                    event
                );

            case IGNORE -> {
                if (
                    turnRecovery.matches(
                        event.clientTurnId
                    )
                ) {
                    scheduleTurnRecoveryRecheck(
                        socketGeneration
                    );
                }
            }
        }
    }

    private void handleDirectTerminalRecovery(
        RealtimeProtocol.Event event,
        String status
    ) {
        if (
            !turnRecovery.matches(
                event.clientTurnId
            )
        ) {
            return;
        }

        TurnRecoveryPolicy.Action action =
            turnRecovery.action(
                event.clientTurnId,
                true,
                status,
                false
            );

        if (
            action
                == TurnRecoveryPolicy.Action
                    .FINISH_CANCELLED
        ) {
            finishRecoveredTerminal(
                "Previous Jarvis request was cancelled"
            );
        } else if (
            action
                == TurnRecoveryPolicy.Action
                    .FINISH_INTERRUPTED
        ) {
            finishRecoveredTerminal(
                "Previous Jarvis request was interrupted"
            );
        }
    }

    private void replayRecoveredTurn(
        int socketGeneration
    ) {
        if (
            socketGeneration != generation
                || !ready
                || !turnRecovery.hasPending()
        ) {
            return;
        }

        WebSocket current = socket;

        if (current == null) return;

        long clientTurnId =
            turnRecovery.clientTurnId();

        String command =
            turnRecovery.text();

        boolean speak =
            turnRecovery.speak();

        try {
            turnStartedAtMs =
                SystemClock.elapsedRealtime();
            firstAudioMeasured = false;
            performance.beginTurn();

            activeClientTurnId =
                clientTurnId;
            audioServerGeneration = 0;

            boolean queued = current.send(
                RealtimeProtocol.text(
                    command,
                    speak,
                    clientTurnId
                )
            );

            if (!queued) {
                performance.abandonTurn();

                failCurrent(
                    socketGeneration,
                    "Jarvis replay transport changed"
                );

                return;
            }

            diagnostics.recordRecovery(
                "Replayed Core-unknown realtime "
                    + "turn="
                    + clientTurnId
            );

            /*
             * Keep external READY deferred. turn.done or another
             * terminal recovery result releases it.
             */

        } catch (Exception exception) {
            post(() -> listener.onError(
                "Could not safely replay request: "
                    + safeMessage(exception)
            ));

            scheduleTurnRecoveryRecheck(
                socketGeneration
            );
        }
    }

    private void finishTrackedTurnDone(
        long clientTurnId
    ) {
        if (
            !turnRecovery.matches(
                clientTurnId
            )
        ) {
            listener.onTurnDone();

            if (
                deferredReadyEvent != null
            ) {
                publishDeferredReady();
            }

            return;
        }

        TurnDeliveryFence.Action action =
            TurnDeliveryFence.afterTerminal(
                true,
                turnRecovery
                    .responseDelivered()
            );

        if (
            action
                == TurnDeliveryFence.Action
                    .RECONCILE_COMPLETED
        ) {
            diagnostics.recordRecovery(
                "Core completed before durable "
                    + "response acknowledgement "
                    + "turn="
                    + clientTurnId
            );

            listener.onStatus(
                "Confirming Jarvis response"
            );

            requestTurnRecovery(
                generation
            );

            return;
        }

        clearDurableRecovery(
            clientTurnId
        );

        turnRecovery.clear();
        activeClientTurnId = 0L;
        recoveryScheduleGeneration++;

        listener.onTurnDone();

        if (
            deferredReadyEvent != null
        ) {
            publishDeferredReady();
        }
    }

    private void restoreCompletedTurn(
        RealtimeProtocol.Event event
    ) {
        long clientTurnId =
            event.clientTurnId;

        boolean alreadyDelivered =
            turnRecovery.responseDelivered();

        diagnostics.recordRecovery(
            "Restored completed realtime turn="
                + clientTurnId
        );

        performance.finishTurn();

        boolean hasResponse =
            event.recoveryText != null
                && !event.recoveryText.isBlank();

        if (alreadyDelivered) {
            clearDurableRecovery(
                clientTurnId
            );

            turnRecovery.clear();
            activeClientTurnId = 0L;
            recoveryScheduleGeneration++;

            post(listener::onTurnDone);
            publishDeferredReady();

            return;
        }

        if (!hasResponse) {
            /*
             * Core proved terminal completion and there is no
             * user-visible response payload to persist.
             */
            clearDurableRecovery(
                clientTurnId
            );

            turnRecovery.clear();
            activeClientTurnId = 0L;
            recoveryScheduleGeneration++;

            post(listener::onTurnDone);
            publishDeferredReady();

            return;
        }

        post(() -> {
            if (
                !turnRecovery.matches(
                    clientTurnId
                )
            ) {
                return;
            }

            boolean persisted;

            try {
                persisted =
                    listener
                        .onBrainResponseDurably(
                            clientTurnId,
                            event.recoveryText,
                            event.recoverySuccess,
                            event.recoveryConversationId
                        );

            } catch (
                Exception exception
            ) {
                persisted = false;

                listener.onError(
                    "Could not persist recovered "
                        + "Jarvis response: "
                        + safeMessage(
                            exception
                        )
                );
            }

            if (!persisted) {
                diagnostics.recordRecovery(
                    "Recovered response still not "
                        + "durable turn="
                        + clientTurnId
                );

                listener.onError(
                    "Jarvis could not save the "
                        + "recovered response safely"
                );

                scheduleTurnRecoveryRecheck(
                    generation
                );

                return;
            }

            if (
                !markDurableResponseDelivered(
                    clientTurnId
                )
            ) {
                diagnostics.recordRecovery(
                    "Recovered response saved but "
                        + "journal checkpoint failed "
                        + "turn="
                        + clientTurnId
                );

                listener.onStatus(
                    "Confirming saved Jarvis response"
                );

                scheduleTurnRecoveryRecheck(
                    generation
                );

                return;
            }

            turnRecovery
                .markResponseDelivered(
                    clientTurnId
                );

            clearDurableRecovery(
                clientTurnId
            );

            turnRecovery.clear();
            activeClientTurnId = 0L;
            recoveryScheduleGeneration++;

            listener.onTurnDone();
            publishDeferredReady();
        });
    }

    private void finishRecoveredTerminal(
        String message
    ) {
        long clientTurnId =
            turnRecovery.clientTurnId();

        diagnostics.recordRecovery(
            message
        );

        clearDurableRecovery(
            clientTurnId
        );

        turnRecovery.clear();
        activeClientTurnId = 0L;
        recoveryScheduleGeneration++;
        performance.abandonTurn();

        post(() -> listener.onStatus(
            message
        ));

        post(listener::onTurnDone);

        publishDeferredReady();
    }

    private void failRecoveredConflict(
        RealtimeProtocol.Event event
    ) {
        long clientTurnId =
            event.clientTurnId;

        clearDurableRecovery(
            clientTurnId
        );

        turnRecovery.clear();
        activeClientTurnId = 0L;
        recoveryScheduleGeneration++;
        performance.abandonTurn();

        post(() -> listener.onError(
            "Jarvis rejected conflicting turn id "
                + clientTurnId
        ));

        post(listener::onTurnDone);

        publishDeferredReady();
    }

    private void abandonRecoveryWithoutReplay(
        String message
    ) {
        long clientTurnId =
            turnRecovery.clientTurnId();

        diagnostics.recordRecovery(
            "Recovery stopped without replay "
                + "turn="
                + clientTurnId
        );

        /*
         * Preserve a durable tombstone. If a future client
         * eventually receives UNKNOWN for this turn it still
         * must not replay it.
         */
        durablyAbandonRecovery(
            clientTurnId
        );

        turnRecovery.clear();
        activeClientTurnId = 0L;
        recoveryScheduleGeneration++;
        performance.abandonTurn();

        post(() -> listener.onError(
            message + " — it was not repeated"
        ));

        post(listener::onTurnDone);

        publishDeferredReady();
    }

    private boolean restoreDurableRecovery() {
        try {
            TurnRecoveryJournal.Snapshot snapshot =
                recoveryJournal.load();

            recoveryJournalReadable = true;

            if (snapshot == null) {
                recoverySnapshot = null;
                return true;
            }

            if (
                !snapshot.matchesIdentity(
                    deviceId,
                    conversationId,
                    endpoint
                )
            ) {
                diagnostics.recordRecovery(
                    "Discarded mismatched realtime recovery journal"
                );

                recoveryJournal.clear();
                recoverySnapshot = null;

                return true;
            }

            recoverySnapshot =
                snapshot;

            turnRecovery.restore(
                snapshot.clientTurnId(),
                snapshot.text(),
                snapshot.speak(),
                snapshot.responseDelivered()
            );

            activeClientTurnId =
                snapshot.clientTurnId();

            diagnostics.recordRecovery(
                "Restored durable realtime turn="
                    + snapshot.clientTurnId()
                    + (snapshot.abandoned()
                        ? " abandoned=true"
                        : "")
            );

            return true;

        } catch (IOException exception) {
            recoveryJournalReadable = false;

            diagnostics.recordRecovery(
                "Realtime recovery journal unavailable: "
                    + safeMessage(exception)
            );

            return false;
        }
    }

    private boolean persistNewRecoverySnapshot(
        TurnRecoveryJournal.Snapshot snapshot
    ) {
        try {
            recoveryJournal.save(
                snapshot
            );

            recoverySnapshot =
                snapshot;

            recoveryJournalReadable = true;

            return true;

        } catch (IOException exception) {
            recoveryJournalReadable = false;

            diagnostics.recordRecovery(
                "Could not persist realtime request: "
                    + safeMessage(exception)
            );

            post(() -> listener.onError(
                "Jarvis could not safely save this request"
            ));

            return false;
        }
    }

    private boolean markDurableResponseDelivered(
        long clientTurnId
    ) {
        if (clientTurnId <= 0L) {
            return true;
        }

        try {
            boolean marked =
                recoveryJournal.markResponseDelivered(
                    clientTurnId,
                    deviceId,
                    conversationId,
                    endpoint
                );

            if (marked) {
                TurnRecoveryJournal.Snapshot snapshot =
                    recoverySnapshot;

                if (
                    snapshot != null
                        && snapshot.clientTurnId()
                            == clientTurnId
                ) {
                    recoverySnapshot =
                        new TurnRecoveryJournal.Snapshot(
                            snapshot.clientTurnId(),
                            snapshot.text(),
                            snapshot.speak(),
                            snapshot.deviceId(),
                            snapshot.conversationId(),
                            snapshot.endpoint(),
                            snapshot.abandoned(),
                            true,
                            snapshot.createdAtMs()
                        );
                }
            }

            return marked;

        } catch (IOException exception) {
            diagnostics.recordRecovery(
                "Could not persist response delivery "
                    + "turn="
                    + clientTurnId
                    + " · "
                    + safeMessage(exception)
            );

            return false;
        }
    }

    private boolean durablyAbandonRecovery(
        long clientTurnId
    ) {
        if (clientTurnId <= 0L) {
            return true;
        }

        try {
            boolean marked =
                recoveryJournal.markAbandoned(
                    clientTurnId,
                    deviceId,
                    conversationId,
                    endpoint
                );

            if (marked) {
                TurnRecoveryJournal.Snapshot snapshot =
                    recoverySnapshot;

                if (
                    snapshot != null
                        && snapshot.clientTurnId()
                            == clientTurnId
                ) {
                    recoverySnapshot =
                        new TurnRecoveryJournal.Snapshot(
                            snapshot.clientTurnId(),
                            snapshot.text(),
                            snapshot.speak(),
                            snapshot.deviceId(),
                            snapshot.conversationId(),
                            snapshot.endpoint(),
                            true,
                            snapshot.responseDelivered(),
                            snapshot.createdAtMs()
                        );
                }

                return true;
            }

            /*
             * No matching replay authority exists. Removing the
             * scoped file is also a safe abandonment result.
             */
            recoveryJournal.clear();
            recoverySnapshot = null;

            return true;

        } catch (IOException exception) {
            diagnostics.recordRecovery(
                "Could not persist realtime abandonment "
                    + "turn="
                    + clientTurnId
                    + " · "
                    + safeMessage(exception)
            );

            /*
             * Best-effort fallback: deletion removes automatic
             * replay authority entirely.
             */
            try {
                recoveryJournal.clear();
                recoverySnapshot = null;
                return true;

            } catch (IOException deleteFailure) {
                diagnostics.recordRecovery(
                    "Could not remove realtime replay authority "
                        + "turn="
                        + clientTurnId
                        + " · "
                        + safeMessage(deleteFailure)
                );

                return false;
            }
        }
    }

    private void clearDurableRecovery(
        long clientTurnId
    ) {
        if (clientTurnId <= 0L) {
            recoverySnapshot = null;
            return;
        }

        try {
            recoveryJournal.clearMatching(
                clientTurnId,
                deviceId,
                conversationId,
                endpoint
            );

            recoverySnapshot = null;
            recoveryJournalReadable = true;

        } catch (IOException exception) {
            /*
             * Core has already proven terminal state. Leaving
             * the journal behind is fail-safe: a future client
             * will query Core rather than execute blindly.
             */
            diagnostics.recordRecovery(
                "Could not clear terminal recovery journal "
                    + "turn="
                    + clientTurnId
                    + " · "
                    + safeMessage(exception)
            );
        }
    }

    private boolean mayReplayUnknown() {
        TurnRecoveryJournal.Snapshot snapshot =
            recoverySnapshot;

        return snapshot != null
            && snapshot.clientTurnId()
                == turnRecovery.clientTurnId()
            && snapshot.matchesIdentity(
                deviceId,
                conversationId,
                endpoint
            )
            && snapshot.mayReplayUnknown(
                System.currentTimeMillis()
            );
    }

    private void finishUnknownWithoutReplay() {
        long clientTurnId =
            turnRecovery.clientTurnId();

        TurnRecoveryJournal.Snapshot snapshot =
            recoverySnapshot;

        String reason;

        if (
            snapshot != null
                && snapshot.abandoned()
        ) {
            reason =
                "Previous Jarvis request was abandoned";
        } else {
            reason =
                "Previous Jarvis request is too old to replay safely";
        }

        /*
         * Core explicitly reported UNKNOWN, so it never admitted
         * this command. It is therefore safe to discard the local
         * replay record without executing anything.
         */
        clearDurableRecovery(
            clientTurnId
        );

        turnRecovery.clear();
        activeClientTurnId = 0L;
        recoveryScheduleGeneration++;
        performance.abandonTurn();

        post(() -> listener.onStatus(
            reason + " — it was not repeated"
        ));

        post(listener::onTurnDone);

        publishDeferredReady();
    }

    private boolean isStaleTurnEvent(RealtimeProtocol.Event event) {
        if (event == null || !isTurnScopedType(event.type)) return false;

        if (event.generation > 0) {
            if (event.generation < minimumServerGeneration) return true;
            if (event.generation < highestServerGeneration) return true;
            if (event.generation > highestServerGeneration) {
                highestServerGeneration = event.generation;
                minimumServerGeneration = Math.max(
                    minimumServerGeneration,
                    highestServerGeneration
                );
            }
        }

        if (event.clientTurnId <= 0L) return false;
        return activeClientTurnId <= 0L || event.clientTurnId != activeClientTurnId;
    }

    private static boolean isTurnScopedType(String type) {
        return switch (type == null ? "" : type) {
            case "brain.started", "brain.delta", "brain.response", "brain.discarded",
                 "tool.started", "tool.completed", "memory.context", "turn.summary",
                 "assistant.transcript.delta", "assistant.transcript.done",
                 "original.tts", "audio.done", "turn.done", "turn.accepted",
                 "turn.status", "turn.conflict", "turn.interrupted",
                 "turn.cancelled", "session.context", "session.close",
                 "speech.early.started", "speech.remainder.ready", "speech.continuation" -> true;
            default -> false;
        };
    }

    private final Runnable authTimeout = () -> {
        if (!authenticated && shouldReconnect) {
            failCurrent(generation, "Jarvis Core authentication timed out");
        }
    };

    private final Runnable readyTimeout = () -> {
        if (!ready && shouldReconnect) {
            failCurrent(generation, "Jarvis voice session did not become ready");
        }
    };

    private final Runnable pingTask = () -> {
        WebSocket current = socket;
        if (!ready || current == null) return;
        pingStartedAtMs = SystemClock.elapsedRealtime();
        current.send(RealtimeProtocol.ping(System.currentTimeMillis()));
    };

    private void scheduleAuthTimeout(int currentGeneration) {
        main.removeCallbacks(authTimeout);
        main.postDelayed(() -> {
            if (currentGeneration == generation) authTimeout.run();
        }, AUTH_TIMEOUT_MS);
    }

    private void scheduleReadyTimeout(int currentGeneration) {
        main.removeCallbacks(readyTimeout);
        main.postDelayed(() -> {
            if (currentGeneration == generation) readyTimeout.run();
        }, READY_TIMEOUT_MS);
    }

    private void schedulePing() {
        main.removeCallbacks(pingTask);
        if (ready) main.postDelayed(pingTask, PING_INTERVAL_MS);
    }

    private void failCurrent(int currentGeneration, String reason) {
        if (currentGeneration != generation) return;
        WebSocket current = socket;
        socket = null;
        opening = false;
        authenticated = false;
        ready = false;
        turnRecovery.onTransportLost();
        generation++;
        cancelTimers();
        if (current != null) current.cancel();
        post(() -> listener.onDisconnected(reason));
        scheduleReconnect(reason);
    }

    private void scheduleReconnect(String reason) {
        if (!shouldReconnect || reconnectScheduled) return;
        if (!network.isAvailable()) {
            diagnostics.recordNetworkStatus(false, "Reconnect paused");
            diagnostics.recordCoreReachability("Unavailable", "No network");
            post(() -> listener.onStatus("Offline — waiting for network"));
            return;
        }

        int attempt = Math.min(7, reconnectAttempt++);
        int seed = (int) (System.nanoTime() ^ generation ^ attempt);
        long delay = ReconnectPolicy.delayMillis(attempt, seed);
        reconnectScheduled = true;
        int scheduleGeneration = ++reconnectScheduleGeneration;
        diagnostics.recordReconnect(attempt + 1, delay, reason);
        diagnostics.recordNetworkStatus(true, "Reconnecting");
        diagnostics.recordCoreReachability("Checking", "Retry scheduled");
        main.postDelayed(() -> {
            if (!reconnectScheduled || scheduleGeneration != reconnectScheduleGeneration) return;
            reconnectScheduled = false;
            reconnectScheduleGeneration++;
            if (shouldReconnect && !authenticated && network.isAvailable()) {
                open();
            }
        }, delay);
    }

    private void networkAvailable() {
        diagnostics.recordNetworkStatus(true, "Network restored");
        diagnostics.recordCoreReachability("Checking", "Selecting endpoint");
        if (authenticated || ready) {
            post(() -> listener.onStatus("Network changed — selecting the best Jarvis route"));
            performance.abandonTurn();
            turnRecovery.onTransportLost();
            authenticated = false;
            ready = false;
            opening = false;
            probing = false;
            generation++;
            endpoints.cancel();
            cancelTimers();
            WebSocket current = socket;
            socket = null;
            if (current != null) current.cancel();
            reconnectAttempt = 0;
            open();
            return;
        }
        post(() -> listener.onStatus("Network restored — reconnecting Jarvis"));
        if (shouldReconnect && !authenticated && !opening) {
            reconnectScheduled = false;
            reconnectScheduleGeneration++;
            reconnectAttempt = 0;
            open();
        }
    }

    private void networkLost() {
        diagnostics.recordNetworkStatus(false, "Network lost");
        diagnostics.recordCoreReachability("Unavailable", "No network");
        endpoints.cancel();
        probing = false;
        turnRecovery.onTransportLost();
        highestServerGeneration = 0;
        minimumServerGeneration = 0;
        authenticated = false;
        reconnectScheduled = false;
        reconnectScheduleGeneration++;
        ready = false;
        opening = false;
        cancelTimers();
        WebSocket current = socket;
        socket = null;
        generation++;
        if (current != null) current.cancel();
        post(() -> listener.onDisconnected("Network unavailable"));
    }

    private final Runnable lanRecheckTask;

    private void scheduleLanRecheck() {
        main.removeCallbacks(lanRecheckTask);
        if (ready && !endpoints.isLan(activeCoreUrl)) {
            main.postDelayed(lanRecheckTask, LAN_RECHECK_MS);
        }
    }

    private void returnToLan(String url, String name) {
        if (!shouldReconnect || !ready || endpoints.isLan(activeCoreUrl)) return;
        diagnostics.recordRecovery("Returned from Tailscale to LAN");
        diagnostics.recordEndpoint(name, url);
        diagnostics.recordCoreReachability("Reachable", "LAN restored");
        post(() -> listener.onStatus("Home network restored — switching to LAN"));

        turnRecovery.onTransportLost();

        WebSocket current = socket;
        socket = null;
        authenticated = false;
        ready = false;
        opening = false;
        probing = false;
        generation++;
        cancelTimers();
        if (current != null) current.cancel();
        openSocket(url, name);
    }

    private void cancelTimers() {
        main.removeCallbacks(authTimeout);
        main.removeCallbacks(readyTimeout);
        main.removeCallbacks(pingTask);
        main.removeCallbacks(lanRecheckTask);
        recoveryScheduleGeneration++;
    }

    private void post(Runnable runnable) {
        main.post(runnable);
    }

    private static String safeMessage(Exception exception) {
        String value = exception.getMessage();
        return value == null || value.isBlank()
            ? exception.getClass().getSimpleName()
            : value;
    }
}
