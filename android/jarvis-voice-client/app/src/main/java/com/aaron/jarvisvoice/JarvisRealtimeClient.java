package com.aaron.jarvisvoice;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;

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
        void onBrainResponse(String text, boolean success, String conversationId);
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
    private final TurnRecoveryState turnRecovery =
        new TurnRecoveryState();
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
        turnIds = new ClientTurnIdStore(context);
        diagnostics = new VoiceDiagnosticsStore(context);
        performance = new TurnPerformanceTracker(diagnostics);
        endpoints = new CoreEndpointSelector(context, coreUrl);
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
        shouldReconnect = false;
        reconnectScheduled = false;
        reconnectScheduleGeneration++;
        performance.abandonTurn();
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
            current.send(RealtimeProtocol.stop());
            current.close(1000, "Jarvis voice stopped");
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
        long cancelledTurnId = activeClientTurnId;
        turnRecovery.clear();
        activeClientTurnId = 0L;
        recoveryScheduleGeneration++;
        audioServerGeneration = 0;
        minimumServerGeneration = Math.max(
            minimumServerGeneration,
            highestServerGeneration + 1
        );
        if (authenticated && current != null) {
            performance.abandonTurn();
            current.send(RealtimeProtocol.cancel(cancelledTurnId));
        }
    }

    public boolean sendText(String text, boolean speak) {
        WebSocket current = socket;

        if (
            !ready
                || current == null
                || text == null
                || text.isBlank()
        ) {
            return false;
        }

        try {
            String command = text.trim();

            turnStartedAtMs =
                SystemClock.elapsedRealtime();
            firstAudioMeasured = false;
            performance.beginTurn();

            long clientTurnId =
                turnIds.next();

            turnRecovery.begin(
                clientTurnId,
                command,
                speak
            );

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

                /*
                 * Recovery now owns this logical request, so
                 * VoiceService must not submit it a second time.
                 */
                return true;
            }

            return true;

        } catch (Exception exception) {
            turnRecovery.clear();
            activeClientTurnId = 0L;

            post(() -> listener.onError(
                "Could not send text: "
                    + safeMessage(exception)
            ));

            return false;
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
                turnRecovery.markResponseDelivered(
                    event.clientTurnId
                );

                post(() -> listener.onBrainResponse(
                    event.text,
                    event.success,
                    event.conversationId
                ));
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
                if (
                    event.clientTurnId > 0L
                        && event.clientTurnId
                            == activeClientTurnId
                ) {
                    turnRecovery.clear();
                    activeClientTurnId = 0L;
                    recoveryScheduleGeneration++;
                }
                if (event.generation > 0) {
                    minimumServerGeneration = Math.max(
                        minimumServerGeneration,
                        event.generation + 1
                    );
                }
                post(listener::onTurnDone);
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
            case REPLAY_UNKNOWN ->
                replayRecoveredTurn(
                    socketGeneration
                );

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

            publishDeferredReady();

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

    private void restoreCompletedTurn(
        RealtimeProtocol.Event event
    ) {
        boolean alreadyDelivered =
            turnRecovery.responseDelivered();

        diagnostics.recordRecovery(
            "Restored completed realtime turn="
                + event.clientTurnId
        );

        turnRecovery.clear();
        activeClientTurnId = 0L;
        recoveryScheduleGeneration++;
        performance.finishTurn();

        publishDeferredReady();

        if (
            !alreadyDelivered
                && event.recoveryText != null
                && !event.recoveryText.isBlank()
        ) {
            post(() -> listener.onBrainResponse(
                event.recoveryText,
                event.recoverySuccess,
                event.recoveryConversationId
            ));
        }

        post(listener::onTurnDone);
    }

    private void finishRecoveredTerminal(
        String message
    ) {
        diagnostics.recordRecovery(
            message
        );

        turnRecovery.clear();
        activeClientTurnId = 0L;
        recoveryScheduleGeneration++;
        performance.abandonTurn();

        publishDeferredReady();

        post(() -> listener.onStatus(
            message
        ));

        post(listener::onTurnDone);
    }

    private void failRecoveredConflict(
        RealtimeProtocol.Event event
    ) {
        long clientTurnId =
            event.clientTurnId;

        turnRecovery.clear();
        activeClientTurnId = 0L;
        recoveryScheduleGeneration++;
        performance.abandonTurn();

        publishDeferredReady();

        post(() -> listener.onError(
            "Jarvis rejected conflicting turn id "
                + clientTurnId
        ));
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
         * Never guess that an admitted or otherwise unresolved
         * side-effecting request is safe to execute again.
         */
        turnRecovery.clear();
        activeClientTurnId = 0L;
        recoveryScheduleGeneration++;
        performance.abandonTurn();

        publishDeferredReady();

        post(() -> listener.onError(
            message + " — it was not repeated"
        ));

        post(listener::onTurnDone);
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
