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
    private final Listener listener;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final VoiceDiagnosticsStore diagnostics;
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
    private int generation;
    private long openStartedAtMs;
    private long pingStartedAtMs;
    private long turnStartedAtMs;
    private boolean firstAudioMeasured;
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
        this.listener = listener;
        diagnostics = new VoiceDiagnosticsStore(context);
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
        if (current.queueSize() > 384_000L) return false;
        return current.send(ByteString.of(pcm16, 0, pcm16.length));
    }

    public void cancelResponse() {
        WebSocket current = socket;
        if (authenticated && current != null) {
            current.send(RealtimeProtocol.cancel());
        }
    }

    public boolean sendText(String text, boolean speak) {
        WebSocket current = socket;
        if (!ready || current == null || text == null || text.isBlank()) return false;
        try {
            turnStartedAtMs = SystemClock.elapsedRealtime();
            firstAudioMeasured = false;
            return current.send(RealtimeProtocol.text(text.trim(), speak));
        } catch (Exception exception) {
            post(() -> listener.onError("Could not send text: " + safeMessage(exception)));
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
                        conversationId
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

        switch (event.type) {
            case "auth.ok" -> {
                authenticated = true;
                reconnectAttempt = 0;
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
                main.removeCallbacks(readyTimeout);
                diagnostics.recordNetworkStatus(true, "Online");
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
                post(() -> listener.onReady(
                    event.model,
                    event.voice,
                    event.voiceMode,
                    event.conversationMode,
                    event.unifiedBrain
                ));
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
            case "speech.started" -> post(listener::onSpeechStarted);
            case "user.transcript" -> {
                turnStartedAtMs = SystemClock.elapsedRealtime();
                firstAudioMeasured = false;
                post(() -> listener.onUserTranscript(event.text));
            }
            case "assistant.transcript.delta" -> post(() -> listener.onAssistantTranscriptDelta(event.text));
            case "assistant.transcript.done" -> post(() -> listener.onAssistantTranscriptDone(event.text));
            case "audio.done" -> post(listener::onAudioDone);
            case "brain.started" -> {
                if (turnStartedAtMs <= 0L) {
                    turnStartedAtMs = SystemClock.elapsedRealtime();
                }
                firstAudioMeasured = false;
                post(() -> listener.onBrainStarted(
                    event.command.isBlank() ? event.text : event.command
                ));
            }
            case "brain.delta" -> post(() -> listener.onBrainDelta(event.text));
            case "brain.response" -> post(() -> listener.onBrainResponse(event.text, event.success, event.conversationId));
            case "original.tts" -> post(() -> listener.onOriginalTts(event.text));
            case "turn.done" -> post(listener::onTurnDone);
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
        generation++;
        cancelTimers();
        if (current != null) current.cancel();
        post(() -> listener.onDisconnected(reason));
        scheduleReconnect(reason);
    }

    private void scheduleReconnect(String reason) {
        if (!shouldReconnect) return;
        if (!network.isAvailable()) {
            diagnostics.recordNetworkStatus(false, "Reconnect paused");
            diagnostics.recordCoreReachability("Unavailable", "No network");
            post(() -> listener.onStatus("Offline — waiting for network"));
            return;
        }

        int attempt = Math.min(7, reconnectAttempt++);
        int seed = (int) (System.nanoTime() ^ generation ^ attempt);
        long delay = ReconnectPolicy.delayMillis(attempt, seed);
        diagnostics.recordReconnect(attempt + 1, delay, reason);
        diagnostics.recordNetworkStatus(true, "Reconnecting");
        diagnostics.recordCoreReachability("Checking", "Retry scheduled");
        main.postDelayed(() -> {
            if (shouldReconnect && !authenticated && network.isAvailable()) {
                open();
            }
        }, delay);
    }

    private void networkAvailable() {
        diagnostics.recordNetworkStatus(true, "Network restored");
        diagnostics.recordCoreReachability("Checking", "Selecting endpoint");
        post(() -> listener.onStatus("Network restored — reconnecting Jarvis"));
        if (shouldReconnect && !authenticated && !opening) {
            reconnectAttempt = 0;
            open();
        }
    }

    private void networkLost() {
        diagnostics.recordNetworkStatus(false, "Network lost");
        diagnostics.recordCoreReachability("Unavailable", "No network");
        endpoints.cancel();
        probing = false;
        authenticated = false;
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
