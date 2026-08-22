package com.aaron.jarvisvoice;

import org.json.JSONObject;

import java.time.OffsetDateTime;
import java.time.ZoneId;

public final class RealtimeProtocol {
    private static final String LOCAL_TIMEZONE = "Europe/London";

    public static final class Event {
        public final String type;
        public final String message;
        public final String text;
        public final String model;
        public final String command;
        public final String voice;
        public final String voiceMode;
        public final String conversationMode;
        public final String conversationId;
        public final String transport;
        public final String phase;
        public final String tool;
        public final String userName;
        public final int sampleRate;
        public final int messageCount;
        public final int generation;
        public final long clientTurnId;
        public final boolean success;
        public final boolean unifiedBrain;
        public final boolean memoryUsed;
        public final boolean toolCalled;

        private Event(
            String type,
            String message,
            String text,
            String model,
            String command,
            String voice,
            String voiceMode,
            String conversationMode,
            String conversationId,
            String transport,
            String phase,
            String tool,
            String userName,
            int sampleRate,
            int messageCount,
            int generation,
            long clientTurnId,
            boolean success,
            boolean unifiedBrain,
            boolean memoryUsed,
            boolean toolCalled
        ) {
            this.type = type;
            this.message = message;
            this.text = text;
            this.model = model;
            this.command = command;
            this.voice = voice;
            this.voiceMode = voiceMode;
            this.conversationMode = conversationMode;
            this.conversationId = conversationId;
            this.transport = transport;
            this.phase = phase;
            this.tool = tool;
            this.userName = userName;
            this.sampleRate = sampleRate;
            this.messageCount = messageCount;
            this.generation = generation;
            this.clientTurnId = clientTurnId;
            this.success = success;
            this.unifiedBrain = unifiedBrain;
            this.memoryUsed = memoryUsed;
            this.toolCalled = toolCalled;
        }
    }

    private RealtimeProtocol() {}

    public static String auth(
        String token,
        String deviceId,
        String userId,
        String userName,
        String voice,
        String voiceMode,
        String conversationMode,
        String vadEagerness,
        String conversationId
    ) throws Exception {
        return auth(
            token, deviceId, userId, userName, voice, voiceMode,
            conversationMode, vadEagerness, conversationId, "PHONE"
        );
    }

    public static String auth(
        String token,
        String deviceId,
        String userId,
        String userName,
        String voice,
        String voiceMode,
        String conversationMode,
        String vadEagerness,
        String conversationId,
        String endpoint
    ) throws Exception {
        return withLocalTime(new JSONObject()
            .put("type", "auth")
            .put("token", token)
            .put("device_id", deviceId)
            .put("user_id", userId)
            .put("user_name", userName)
            .put("voice", voice)
            .put("voice_mode", voiceMode)
            .put("conversation_mode", ConversationMode.normalise(conversationMode))
            .put("vad_eagerness", vadEagerness)
            .put("conversation_id", conversationId)
            .put("endpoint", "WATCH".equalsIgnoreCase(endpoint) ? "WATCH" : "PHONE")
            .put("transport", "websocket_pcm")
            .put("protocol_version", JarvisVersion.REALTIME_PROTOCOL)
            .put("client_release", JarvisVersion.RELEASE))
            .toString();
    }

    public static String ping(long clientTimeMs) {
        try {
            return withLocalTime(new JSONObject()
                .put("type", "ping")
                .put("client_time_ms", clientTimeMs))
                .toString();
        } catch (Exception ignored) {
            return "{\"type\":\"ping\"}";
        }
    }

    public static String cancel() {
        return cancel(0L);
    }

    public static String cancel(long clientTurnId) {
        try {
            JSONObject payload = new JSONObject().put("type", "cancel");
            if (clientTurnId > 0L) payload.put("client_turn_id", clientTurnId);
            return withLocalTime(payload).toString();
        } catch (Exception ignored) {
            return "{\"type\":\"cancel\"}";
        }
    }

    public static String stop() {
        try {
            return withLocalTime(new JSONObject().put("type", "stop"))
                .toString();
        } catch (Exception ignored) {
            return "{\"type\":\"stop\"}";
        }
    }

    public static String text(String value, boolean speak) throws Exception {
        return text(value, speak, 0L);
    }

    public static String text(String value, boolean speak, long clientTurnId) throws Exception {
        JSONObject payload = new JSONObject()
            .put("type", "text")
            .put("text", value)
            .put("speak", speak);
        if (clientTurnId > 0L) payload.put("client_turn_id", clientTurnId);
        return withLocalTime(payload).toString();
    }

    private static JSONObject withLocalTime(JSONObject payload) throws Exception {
        OffsetDateTime local = OffsetDateTime.now(ZoneId.of(LOCAL_TIMEZONE));
        return payload
            .put("timezone", LOCAL_TIMEZONE)
            .put("local_datetime", local.toString())
            .put("utc_offset_seconds", local.getOffset().getTotalSeconds());
    }

    public static Event parse(String raw) throws Exception {
        JSONObject root = new JSONObject(raw);
        return new Event(
            root.optString("type", "unknown"),
            root.optString("message", ""),
            root.optString("text", ""),
            root.optString("model", ""),
            root.optString("command", ""),
            root.optString("voice", ""),
            root.optString("voice_mode", ""),
            root.optString("conversation_mode", ""),
            root.optString("conversation_id", ""),
            root.optString("transport", "websocket_pcm"),
            root.optString("phase", ""),
            root.optString("tool", ""),
            root.optString("user_name", ""),
            root.optInt("sample_rate", 24_000),
            root.optInt("message_count", 0),
            root.optInt("generation", 0),
            root.optLong("client_turn_id", 0L),
            root.optBoolean("success", true),
            root.optBoolean("unified_brain", false),
            root.optBoolean("memory_used", false),
            root.optBoolean("tool_called", false)
        );
    }
}
