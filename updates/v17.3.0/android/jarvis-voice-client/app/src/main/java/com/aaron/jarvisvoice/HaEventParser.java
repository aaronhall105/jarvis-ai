package com.aaron.jarvisvoice;

import org.json.JSONObject;

public final class HaEventParser {
    public static final class ParsedEvent {
        public String type = "";
        public String textDelta = "";
        public String speech = "";
        public String ttsUrl = "";
        public String conversationId = "";
        public boolean continueConversation = false;
        public String error = "";
    }

    private HaEventParser() {}

    public static ParsedEvent parse(JSONObject root) {
        ParsedEvent parsed = new ParsedEvent();
        if (!"event".equals(root.optString("type"))) {
            return parsed;
        }
        JSONObject event = root.optJSONObject("event");
        if (event == null) {
            return parsed;
        }
        parsed.type = event.optString("type");
        JSONObject data = event.optJSONObject("data");
        if (data == null) {
            return parsed;
        }

        if ("intent-progress".equals(parsed.type)) {
            JSONObject delta = data.optJSONObject("chat_log_delta");
            if (delta != null) {
                parsed.textDelta = delta.optString("content");
            }
        } else if ("intent-end".equals(parsed.type)) {
            JSONObject output = data.optJSONObject("intent_output");
            if (output != null) {
                parsed.conversationId = output.optString("conversation_id");
                parsed.continueConversation = output.optBoolean("continue_conversation", false);
                JSONObject response = output.optJSONObject("response");
                JSONObject speech = response == null ? null : response.optJSONObject("speech");
                JSONObject plain = speech == null ? null : speech.optJSONObject("plain");
                if (plain != null) {
                    parsed.speech = plain.optString("speech");
                }
            }
        } else if ("run-start".equals(parsed.type) || "tts-end".equals(parsed.type)) {
            JSONObject tts = data.optJSONObject("tts_output");
            if (tts != null) {
                parsed.ttsUrl = tts.optString("url");
            }
        } else if ("error".equals(parsed.type)) {
            parsed.error = data.optString("message", data.optString("code", "Assist pipeline error"));
        }
        return parsed;
    }
}
