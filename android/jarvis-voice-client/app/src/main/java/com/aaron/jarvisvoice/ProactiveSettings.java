package com.aaron.jarvisvoice;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.LinkedHashMap;
import java.util.Map;

public final class ProactiveSettings {
    public static final String[] CATEGORIES = {
        "security", "cameras", "appliances", "energy",
        "batteries", "presence", "system",
    };

    public final String userId;
    public boolean enabled;
    public int minImportance;
    public boolean notifyEnabled;
    public boolean speakEnabled;
    public final Map<String, Boolean> categories;

    public ProactiveSettings(
        String userId,
        boolean enabled,
        int minImportance,
        boolean notifyEnabled,
        boolean speakEnabled,
        Map<String, Boolean> categories
    ) {
        this.userId = userId == null ? "aaron" : userId;
        this.enabled = enabled;
        this.minImportance = Math.max(0, Math.min(100, minImportance));
        this.notifyEnabled = notifyEnabled;
        this.speakEnabled = speakEnabled;
        this.categories = new LinkedHashMap<>();
        for (String category : CATEGORIES) {
            this.categories.put(
                category,
                categories.getOrDefault(category, true)
            );
        }
    }

    public static ProactiveSettings fromJson(
        JSONObject item,
        String fallbackUser
    ) {
        Map<String, Boolean> categories = new LinkedHashMap<>();
        JSONObject source = item.optJSONObject("categories");
        for (String category : CATEGORIES) {
            categories.put(
                category,
                source == null || source.optBoolean(category, true)
            );
        }
        return new ProactiveSettings(
            item.optString("user_id", fallbackUser),
            item.optBoolean("enabled", true),
            item.optInt("min_importance", 80),
            item.optBoolean("notify_enabled", true),
            item.optBoolean("speak_enabled", false),
            categories
        );
    }

    public JSONObject toJson() {
        try {
            JSONObject categoriesJson = new JSONObject();
            for (Map.Entry<String, Boolean> entry : categories.entrySet()) {
                categoriesJson.put(
                    entry.getKey(),
                    entry.getValue()
                );
            }

            JSONObject payload = new JSONObject();
            payload.put("user_id", userId);
            payload.put("enabled", enabled);
            payload.put("min_importance", minImportance);
            payload.put("notify_enabled", notifyEnabled);
            payload.put("speak_enabled", speakEnabled);
            payload.put("quiet_start_hour", 22);
            payload.put("quiet_end_hour", 7);
            payload.put("categories", categoriesJson);
            return payload;
        } catch (JSONException exception) {
            throw new IllegalStateException(
                "Unable to create proactive settings JSON",
                exception
            );
        }
    }
}
