package com.aaron.jarvisvoice;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

public final class ProactiveEvent {
    public final String id;
    public final String category;
    public final String entityId;
    public final String title;
    public final String message;
    public final String reason;
    public final int importance;
    public final List<String> actions;
    public final String status;
    public final long createdAt;
    public final Long snoozedUntil;

    public ProactiveEvent(
        String id,
        String category,
        String entityId,
        String title,
        String message,
        String reason,
        int importance,
        List<String> actions,
        String status,
        long createdAt,
        Long snoozedUntil
    ) {
        this.id = safe(id);
        this.category = safe(category);
        this.entityId = safe(entityId);
        this.title = safe(title);
        this.message = safe(message);
        this.reason = safe(reason);
        this.importance = Math.max(0, Math.min(100, importance));
        this.actions = List.copyOf(actions);
        this.status = safe(status);
        this.createdAt = createdAt;
        this.snoozedUntil = snoozedUntil;
    }

    public boolean hasAction(String value) {
        return actions.contains(value);
    }

    public static ProactiveEvent fromJson(JSONObject item) {
        List<String> actions = new ArrayList<>();
        JSONArray source = item.optJSONArray("actions");
        if (source != null) {
            for (int index = 0; index < source.length(); index++) {
                String value = source.optString(index, "").trim();
                if (!value.isBlank()) actions.add(value);
            }
        }
        return new ProactiveEvent(
            item.optString("id", ""),
            item.optString("category", "system"),
            item.optString("entity_id", ""),
            item.optString("title", "Jarvis update"),
            item.optString("message", ""),
            item.optString("reason", ""),
            item.optInt("importance", 0),
            actions,
            item.optString("status", "active"),
            item.optLong("created_at", 0L),
            item.isNull("snoozed_until")
                ? null
                : item.optLong("snoozed_until")
        );
    }

    private static String safe(String value) {
        return value == null ? "" : value.trim();
    }
}
