package com.aaron.jarvisvoice;

import org.json.JSONObject;

public final class RealtimeProtocol {
    public static final class Event {
        public final String type;
        public final String message;
        public final String text;
        public final String model;
        public final String command;
        public final String voice;
        public final String voiceMode;
        public final int sampleRate;
        public final boolean success;
        public final boolean unifiedBrain;

        private Event(
            String type,
            String message,
            String text,
            String model,
            String command,
            String voice,
            String voiceMode,
            int sampleRate,
            boolean success,
            boolean unifiedBrain
        ) {
            this.type = type;
            this.message = message;
            this.text = text;
            this.model = model;
            this.command = command;
            this.voice = voice;
            this.voiceMode = voiceMode;
            this.sampleRate = sampleRate;
            this.success = success;
            this.unifiedBrain = unifiedBrain;
        }
    }

    private RealtimeProtocol() {}

    public static String auth(
        String token,
        String deviceId,
        String userName,
        String voice,
        String voiceMode
    ) throws Exception {
        return new JSONObject()
            .put("type", "auth")
            .put("token", token)
            .put("device_id", deviceId)
            .put("user_id", "aaron")
            .put("user_name", userName)
            .put("voice", voice)
            .put("voice_mode", voiceMode)
            .toString();
    }

    public static String ping() {
        try {
            return new JSONObject().put("type", "ping").toString();
        } catch (Exception ignored) {
            return "{\"type\":\"ping\"}";
        }
    }

    public static String cancel() {
        return "{\"type\":\"cancel\"}";
    }

    public static String stop() {
        return "{\"type\":\"stop\"}";
    }

    public static String text(String value) throws Exception {
        return new JSONObject().put("type", "text").put("text", value).toString();
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
            root.optInt("sample_rate", 24_000),
            root.optBoolean("success", true),
            root.optBoolean("unified_brain", false)
        );
    }
}
