package com.aaron.jarvisvoice;

import android.os.Handler;
import android.os.Looper;

import org.json.JSONObject;

import java.net.URI;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;

public final class HaWebSocketClient {
    public interface Listener {
        void onConnected();
        void onDisconnected(String reason);
        void onTextDelta(String delta);
        void onIntentEnd(String speech, String conversationId, boolean continueConversation);
        void onTtsUrl(String url);
        void onRunEnded();
        void onError(String error);
    }

    private final String baseUrl;
    private final String token;
    private final String pipeline;
    private final String deviceId;
    private final Listener listener;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final AtomicInteger ids = new AtomicInteger(1);
    private final OkHttpClient client = new OkHttpClient.Builder()
        .pingInterval(25, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build();
    private WebSocket socket;
    private boolean authenticated;
    private boolean shouldReconnect;
    private int activeRunId;

    public HaWebSocketClient(String baseUrl, String token, String pipeline, String deviceId, Listener listener) {
        this.baseUrl = baseUrl;
        this.token = token;
        this.pipeline = pipeline == null ? "" : pipeline.trim();
        this.deviceId = deviceId;
        this.listener = listener;
    }

    public void connect() {
        shouldReconnect = true;
        openSocket();
    }

    public void close() {
        shouldReconnect = false;
        authenticated = false;
        if (socket != null) socket.close(1000, "Jarvis stopped");
        socket = null;
    }

    public boolean isAuthenticated() { return authenticated; }

    public void sendCommand(String text, String conversationId) {
        if (!authenticated || socket == null) {
            listener.onError("Home Assistant is not connected");
            return;
        }
        cancelActiveRun();
        int id = ids.getAndIncrement();
        activeRunId = id;
        try {
            JSONObject message = new JSONObject()
                .put("id", id)
                .put("type", "assist_pipeline/run")
                .put("start_stage", "intent")
                .put("end_stage", "tts")
                .put("input", new JSONObject().put("text", text))
                .put("conversation_id", conversationId == null || conversationId.isBlank() ? JSONObject.NULL : conversationId)
                .put("device_id", deviceId);
            if (!pipeline.isBlank() && !"preferred".equalsIgnoreCase(pipeline)) {
                message.put("pipeline", pipeline);
            }
            socket.send(message.toString());
        } catch (Exception exception) {
            listener.onError("Could not create Assist request: " + exception.getMessage());
        }
    }

    public void cancelActiveRun() {
        if (activeRunId <= 0 || socket == null || !authenticated) return;
        try {
            socket.send(new JSONObject()
                .put("id", ids.getAndIncrement())
                .put("type", "unsubscribe_events")
                .put("subscription", activeRunId)
                .toString());
        } catch (Exception ignored) {}
        activeRunId = 0;
    }

    private void openSocket() {
        try {
            Request request = new Request.Builder().url(webSocketUrl(baseUrl)).build();
            socket = client.newWebSocket(request, new WebSocketListener() {
                @Override public void onOpen(WebSocket webSocket, Response response) {
                    post(() -> listener.onDisconnected("Authenticating"));
                }

                @Override public void onMessage(WebSocket webSocket, String text) {
                    handleMessage(text);
                }

                @Override public void onClosed(WebSocket webSocket, int code, String reason) {
                    authenticated = false;
                    post(() -> listener.onDisconnected(reason));
                    reconnectLater();
                }

                @Override public void onFailure(WebSocket webSocket, Throwable throwable, Response response) {
                    authenticated = false;
                    post(() -> listener.onDisconnected(throwable.getMessage()));
                    reconnectLater();
                }
            });
        } catch (Exception exception) {
            listener.onError("Invalid Home Assistant URL: " + exception.getMessage());
            reconnectLater();
        }
    }

    private void handleMessage(String raw) {
        try {
            JSONObject root = new JSONObject(raw);
            String type = root.optString("type");
            if ("auth_required".equals(type)) {
                socket.send(new JSONObject().put("type", "auth").put("access_token", token).toString());
                return;
            }
            if ("auth_ok".equals(type)) {
                authenticated = true;
                post(listener::onConnected);
                return;
            }
            if ("auth_invalid".equals(type)) {
                authenticated = false;
                post(() -> listener.onError("Home Assistant rejected the access token"));
                return;
            }
            if ("result".equals(type) && !root.optBoolean("success", true)) {
                JSONObject error = root.optJSONObject("error");
                String message = error == null ? "Home Assistant request failed" : error.optString("message", "Home Assistant request failed");
                post(() -> listener.onError(message));
                return;
            }
            if (!"event".equals(type) || root.optInt("id") != activeRunId) return;

            HaEventParser.ParsedEvent event = HaEventParser.parse(root);
            switch (event.type) {
                case "intent-progress" -> {
                    if (!event.textDelta.isBlank()) post(() -> listener.onTextDelta(event.textDelta));
                }
                case "intent-end" -> post(() -> listener.onIntentEnd(
                    event.speech, event.conversationId, event.continueConversation
                ));
                case "run-start", "tts-end" -> {
                    if (!event.ttsUrl.isBlank()) post(() -> listener.onTtsUrl(event.ttsUrl));
                }
                case "run-end" -> {
                    activeRunId = 0;
                    post(listener::onRunEnded);
                }
                case "error" -> post(() -> listener.onError(event.error));
                default -> { }
            }
        } catch (Exception exception) {
            post(() -> listener.onError("Invalid Home Assistant response: " + exception.getMessage()));
        }
    }

    private void reconnectLater() {
        if (!shouldReconnect) return;
        main.postDelayed(() -> {
            if (shouldReconnect && !authenticated) openSocket();
        }, 2500);
    }

    private void post(Runnable runnable) { main.post(runnable); }

    static String webSocketUrl(String baseUrl) throws Exception {
        URI base = URI.create(baseUrl);
        String scheme = "https".equalsIgnoreCase(base.getScheme()) ? "wss" : "ws";
        String path = base.getPath() == null ? "" : base.getPath();
        if (path.endsWith("/")) path = path.substring(0, path.length() - 1);
        return new URI(scheme, base.getUserInfo(), base.getHost(), base.getPort(), path + "/api/websocket", null, null).toString();
    }
}
