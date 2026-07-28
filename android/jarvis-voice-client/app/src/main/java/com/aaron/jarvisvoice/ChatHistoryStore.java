package com.aaron.jarvisvoice;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

public final class ChatHistoryStore {
    private static final String PREFS = "jarvis_chat_history";
    private static final String KEY_MESSAGES = "messages_v1800";
    private static final int MAX_MESSAGES = 200;

    private final SharedPreferences preferences;

    public ChatHistoryStore(Context context) {
        preferences = context.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    public synchronized List<ChatMessage> list() {
        List<ChatMessage> messages = new ArrayList<>();
        String raw = preferences.getString(KEY_MESSAGES, "[]");
        try {
            JSONArray array = new JSONArray(raw);
            for (int i = 0; i < array.length(); i++) {
                JSONObject item = array.optJSONObject(i);
                if (item == null) continue;
                String text = item.optString("text", "").trim();
                if (text.isEmpty()) continue;
                messages.add(new ChatMessage(
                    item.optString("role", ChatMessage.SYSTEM),
                    text,
                    item.optLong("created_at", 0L)
                ));
            }
        } catch (Exception ignored) {}
        return messages;
    }

    public synchronized void add(String role, String text) {
        String cleaned = text == null ? "" : text.trim();
        if (cleaned.isEmpty()) return;
        List<ChatMessage> messages = list();
        messages.add(new ChatMessage(role, cleaned, System.currentTimeMillis()));
        int start = Math.max(0, messages.size() - MAX_MESSAGES);
        JSONArray array = new JSONArray();
        for (int i = start; i < messages.size(); i++) {
            ChatMessage message = messages.get(i);
            try {
                array.put(new JSONObject()
                    .put("role", message.role)
                    .put("text", message.text)
                    .put("created_at", message.createdAt));
            } catch (Exception ignored) {}
        }
        preferences.edit().putString(KEY_MESSAGES, array.toString()).apply();
    }

    public synchronized void clear() {
        preferences.edit().remove(KEY_MESSAGES).apply();
    }
}
