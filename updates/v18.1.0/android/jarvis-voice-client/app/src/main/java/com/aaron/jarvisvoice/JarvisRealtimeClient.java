package com.aaron.jarvisvoice;

import android.os.Handler;
import android.os.Looper;

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
        void onError(String message);
    }

    private final String coreUrl;
    private final String token;
    private final String deviceId;
    private final String userName;
    private final String voice;
    private final String voiceMode;
    private final String conversationMode;
    private final String vadEagerness;
    private final String conversationId;
    private final Listener listener;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final OkHttpClient http = new OkHttpClient.Builder()
        .pingInterval(12, TimeUnit.SECONDS)
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build();

    private WebSocket socket;
    private boolean authenticated;
    private boolean shouldReconnect;
    private int reconnectAttempt;
    private int generation;

    public JarvisRealtimeClient(
        String coreUrl,
        String token,
        String deviceId,
        String userName,
        String voice,
        String voiceMode,
        String conversationMode,
        String vadEagerness,
        String conversationId,
        Listener listener
    ) {
        this.coreUrl = coreUrl;
        this.token = token;
        this.deviceId = deviceId;
        this.userName = userName;
        this.voice = voice;
        this.voiceMode = voiceMode;
        this.conversationMode = ConversationMode.normalise(conversationMode);
        this.vadEagerness = vadEagerness;
        this.conversationId = conversationId;
        this.listener = listener;
    }

    public void connect() {
        shouldReconnect = true;
        reconnectAttempt = 0;
        open();
    }

    public void close() {
        shouldReconnect = false;
        authenticated = false;
        generation++;
        main.removeCallbacksAndMessages(null);
        WebSocket current = socket;
        socket = null;
        if (current != null) {
            current.send(RealtimeProtocol.stop());
            current.close(1000, "Jarvis voice stopped");
        }
    }

    public boolean sendAudio(byte[] pcm16) {
        WebSocket current = socket;
        if (!authenticated || current == null || pcm16 == null || pcm16.length == 0) return false;
        if (current.queueSize() > 384_000L) return false;
        return current.send(ByteString.of(pcm16, 0, pcm16.length));
    }

    public void cancelResponse() {
        WebSocket current = socket;
        if (authenticated && current != null) current.send(RealtimeProtocol.cancel());
    }

    public boolean sendText(String text, boolean speak) {
        WebSocket current = socket;
        if (!authenticated || current == null || text == null || text.isBlank()) return false;
        try {
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
        final int currentGeneration = ++generation;
        final String websocketUrl;
        try {
            websocketUrl = CoreUrl.websocket(coreUrl);
        } catch (Exception exception) {
            post(() -> listener.onError(safeMessage(exception)));
            return;
        }
        post(() -> listener.onStatus("Connecting to Jarvis Core"));
        Request request = new Request.Builder().url(websocketUrl).build();
        socket = http.newWebSocket(request, new WebSocketListener() {
            @Override public void onOpen(WebSocket webSocket, Response response) {
                if (currentGeneration != generation) return;
                try {
                    webSocket.send(RealtimeProtocol.auth(
                        token,
                        deviceId,
                        userName,
                        voice,
                        voiceMode,
                        conversationMode,
                        vadEagerness,
                        conversationId
                    ));
                } catch (Exception exception) {
                    post(() -> listener.onError("Could not authenticate: " + safeMessage(exception)));
                }
            }

            @Override public void onMessage(WebSocket webSocket, String text) {
                if (currentGeneration != generation) return;
                handleText(text);
            }

            @Override public void onMessage(WebSocket webSocket, ByteString bytes) {
                if (currentGeneration != generation) return;
                byte[] audio = bytes.toByteArray();
                post(() -> listener.onAudio(audio));
            }

            @Override public void onClosed(WebSocket webSocket, int code, String reason) {
                if (currentGeneration != generation) return;
                authenticated = false;
                post(() -> listener.onDisconnected(reason == null || reason.isBlank() ? "Connection closed" : reason));
                scheduleReconnect();
            }

            @Override public void onFailure(WebSocket webSocket, Throwable throwable, Response response) {
                if (currentGeneration != generation) return;
                authenticated = false;
                String reason = throwable == null ? "Connection failed" : throwable.getMessage();
                post(() -> listener.onDisconnected(reason == null ? "Connection failed" : reason));
                scheduleReconnect();
            }
        });
    }

    private void handleText(String raw) {
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
                post(listener::onConnected);
            }
            case "auth.error" -> {
                authenticated = false;
                shouldReconnect = false;
                post(() -> listener.onError(event.message.isBlank() ? "Mobile voice token rejected" : event.message));
            }
            case "ready" -> post(() -> listener.onReady(
                event.model,
                event.voice,
                event.voiceMode,
                event.conversationMode,
                event.unifiedBrain
            ));
            case "status" -> post(() -> listener.onStatus(event.message));
            case "speech.started" -> post(listener::onSpeechStarted);
            case "user.transcript" -> post(() -> listener.onUserTranscript(event.text));
            case "assistant.transcript.delta" -> post(() -> listener.onAssistantTranscriptDelta(event.text));
            case "assistant.transcript.done" -> post(() -> listener.onAssistantTranscriptDone(event.text));
            case "audio.done" -> post(listener::onAudioDone);
            case "brain.started" -> post(() -> listener.onBrainStarted(
                event.command.isBlank() ? event.text : event.command
            ));
            case "brain.delta" -> post(() -> listener.onBrainDelta(event.text));
            case "brain.response" -> post(() -> listener.onBrainResponse(event.text, event.success, event.conversationId));
            case "original.tts" -> post(() -> listener.onOriginalTts(event.text));
            case "turn.done" -> post(listener::onTurnDone);
            case "error" -> post(() -> listener.onError(event.message.isBlank() ? "Jarvis voice error" : event.message));
            default -> { }
        }
    }

    private void scheduleReconnect() {
        if (!shouldReconnect) return;
        int attempt = Math.min(6, reconnectAttempt++);
        long delay = Math.min(8_000L, 500L * (1L << attempt));
        main.postDelayed(() -> {
            if (shouldReconnect && !authenticated) open();
        }, delay);
    }

    private void post(Runnable runnable) {
        main.post(runnable);
    }

    private static String safeMessage(Exception exception) {
        String value = exception.getMessage();
        return value == null || value.isBlank() ? exception.getClass().getSimpleName() : value;
    }
}
