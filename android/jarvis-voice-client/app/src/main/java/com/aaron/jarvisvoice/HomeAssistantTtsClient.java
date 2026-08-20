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

public final class HomeAssistantTtsClient {
    public interface Listener {
        void onHomeAssistantTtsConnected();
        void onHomeAssistantTtsUrl(String url);
        void onHomeAssistantTtsDone();
        void onHomeAssistantTtsError(String message);
    }

    private final String baseUrl;
    private final String token;
    private final String pipeline;
    private final String deviceId;
    private final Listener listener;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final AtomicInteger ids = new AtomicInteger(1);
    private final OkHttpClient http = new OkHttpClient.Builder()
        .pingInterval(25, TimeUnit.SECONDS)
        .connectTimeout(12, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build();

    private WebSocket socket;
    private boolean authenticated;
    private boolean shouldReconnect;
    private int activeRunId;
    private String pendingText = "";

    public HomeAssistantTtsClient(
        String baseUrl,
        String token,
        String pipeline,
        String deviceId,
        Listener listener
    ) {
        this.baseUrl = baseUrl == null ? "" : baseUrl.trim();
        this.token = token == null ? "" : token.trim();
        this.pipeline = pipeline == null ? "" : pipeline.trim();
        this.deviceId = deviceId == null ? "jarvis_android" : deviceId.trim();
        this.listener = listener;
    }

    public void connect() {
        shouldReconnect = true;
        open();
    }

    public void close() {
        shouldReconnect = false;
        cancelActiveRun();
        authenticated = false;
        pendingText = "";
        WebSocket current = socket;
        socket = null;
        if (current != null) current.close(1000, "Jarvis original voice stopped");
        main.removeCallbacksAndMessages(null);
    }

    public void speak(String text) {
        String value = text == null ? "" : text.trim();
        if (value.isEmpty()) return;
        pendingText = value;
        if (!authenticated || socket == null) {
            connect();
            return;
        }
        sendTts(value);
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

    public boolean isAuthenticated() {
        return authenticated;
    }

    private void open() {
        if (socket != null || baseUrl.isBlank() || token.isBlank()) return;
        try {
            Request request = new Request.Builder().url(websocketUrl(baseUrl)).build();
            socket = http.newWebSocket(request, new WebSocketListener() {
                @Override public void onMessage(WebSocket webSocket, String text) {
                    handle(text);
                }

                @Override public void onClosed(WebSocket webSocket, int code, String reason) {
                    socket = null;
                    authenticated = false;
                    reconnectLater();
                }

                @Override public void onFailure(WebSocket webSocket, Throwable throwable, Response response) {
                    socket = null;
                    authenticated = false;
                    String reason = throwable == null ? "Home Assistant TTS connection failed" : throwable.getMessage();
                    post(() -> listener.onHomeAssistantTtsError(reason == null ? "Home Assistant TTS connection failed" : reason));
                    reconnectLater();
                }
            });
        } catch (Exception exception) {
            socket = null;
            post(() -> listener.onHomeAssistantTtsError("Invalid Home Assistant URL: " + safeMessage(exception)));
        }
    }

    private void handle(String raw) {
        try {
            JSONObject root = new JSONObject(raw);
            String type = root.optString("type");
            if ("auth_required".equals(type)) {
                socket.send(new JSONObject().put("type", "auth").put("access_token", token).toString());
                return;
            }
            if ("auth_ok".equals(type)) {
                authenticated = true;
                post(listener::onHomeAssistantTtsConnected);
                String queued = pendingText;
                if (!queued.isBlank()) sendTts(queued);
                return;
            }
            if ("auth_invalid".equals(type)) {
                authenticated = false;
                shouldReconnect = false;
                post(() -> listener.onHomeAssistantTtsError("Home Assistant rejected the access token"));
                return;
            }
            if ("result".equals(type) && !root.optBoolean("success", true)) {
                if (!isFailureForActiveRun(root, activeRunId)) return;
                JSONObject error = root.optJSONObject("error");
                String message = error == null
                    ? "Home Assistant TTS request failed"
                    : error.optString("message", "Home Assistant TTS request failed");
                activeRunId = 0;
                pendingText = "";
                post(() -> listener.onHomeAssistantTtsError(message));
                return;
            }
            if (!"event".equals(type) || root.optInt("id") != activeRunId) return;
            JSONObject event = root.optJSONObject("event");
            if (event == null) return;
            String eventType = event.optString("type");
            JSONObject data = event.optJSONObject("data");
            if ("run-start".equals(eventType) || "tts-end".equals(eventType)) {
                JSONObject tts = data == null ? null : data.optJSONObject("tts_output");
                String url = tts == null ? "" : tts.optString("url");
                if (!url.isBlank()) post(() -> listener.onHomeAssistantTtsUrl(url));
            } else if ("run-end".equals(eventType)) {
                activeRunId = 0;
                pendingText = "";
                post(listener::onHomeAssistantTtsDone);
            } else if ("error".equals(eventType)) {
                String message = data == null
                    ? "Home Assistant Assist pipeline error"
                    : data.optString("message", data.optString("code", "Home Assistant Assist pipeline error"));
                activeRunId = 0;
                pendingText = "";
                post(() -> listener.onHomeAssistantTtsError(message));
            }
        } catch (Exception exception) {
            activeRunId = 0;
            pendingText = "";
            post(() -> listener.onHomeAssistantTtsError("Invalid Home Assistant TTS response: " + safeMessage(exception)));
        }
    }

    private void sendTts(String text) {
        if (!authenticated || socket == null) return;
        cancelActiveRun();
        int id = ids.getAndIncrement();
        activeRunId = id;
        pendingText = text;
        try {
            JSONObject message = new JSONObject()
                .put("id", id)
                .put("type", "assist_pipeline/run")
                .put("start_stage", "tts")
                .put("end_stage", "tts")
                .put("input", new JSONObject().put("text", text))
                .put("device_id", deviceId);
            if (!pipeline.isBlank() && !"preferred".equalsIgnoreCase(pipeline)) {
                message.put("pipeline", pipeline);
            }
            socket.send(message.toString());
        } catch (Exception exception) {
            post(() -> listener.onHomeAssistantTtsError("Could not request original Jarvis voice: " + safeMessage(exception)));
        }
    }

    private void reconnectLater() {
        if (!shouldReconnect || pendingText.isBlank()) return;
        main.postDelayed(() -> {
            if (shouldReconnect && !authenticated && socket == null) open();
        }, 2_500L);
    }

    private void post(Runnable runnable) {
        main.post(runnable);
    }

    static boolean isFailureForActiveRun(JSONObject result, int activeRunId) {
        return result != null
            && activeRunId > 0
            && result.optInt("id", -1) == activeRunId
            && "result".equals(result.optString("type"))
            && !result.optBoolean("success", true);
    }

    static String websocketUrl(String baseUrl) throws Exception {
        URI base = URI.create(baseUrl);
        String scheme = "https".equalsIgnoreCase(base.getScheme()) ? "wss" : "ws";
        if (!("https".equalsIgnoreCase(base.getScheme()) || "http".equalsIgnoreCase(base.getScheme()))) {
            throw new IllegalArgumentException("Home Assistant URL must start with http:// or https://");
        }
        String path = base.getPath() == null ? "" : base.getPath();
        while (path.endsWith("/")) path = path.substring(0, path.length() - 1);
        return new URI(
            scheme,
            base.getUserInfo(),
            base.getHost(),
            base.getPort(),
            path + "/api/websocket",
            null,
            null
        ).toString();
    }

    private static String safeMessage(Exception exception) {
        String value = exception.getMessage();
        return value == null || value.isBlank() ? exception.getClass().getSimpleName() : value;
    }
}
