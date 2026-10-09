package com.aaron.jarvisvoice;

import org.json.JSONArray;
import org.json.JSONObject;

/** Immutable wrapper for the Core-owned HomeExperience presentation contract. */
final class HomeExperience {
    private final JSONObject value;

    private HomeExperience(JSONObject value) {
        this.value = value;
    }

    static HomeExperience fromJson(JSONObject value) {
        if (value == null || value.optInt("schema_version", 0) != 1) {
            throw new IllegalArgumentException("Unsupported HomeExperience response");
        }
        return new HomeExperience(value);
    }

    JSONObject json() { return value; }
    String revision() { return text(value, "revision", ""); }
    String generatedAt() { return text(value, "generated_at", ""); }

    String headline() {
        JSONObject status = value.optJSONObject("overall_status");
        return text(status, "headline", "Home state unavailable");
    }

    String summaryDetail() {
        return text(value.optJSONObject("overall_status"), "detail", "");
    }

    String overallStatus() {
        JSONObject status = value.optJSONObject("overall_status");
        return text(status, "status", "UNAVAILABLE");
    }

    String freshnessStatus() {
        JSONObject freshness = value.optJSONObject("source_freshness");
        return text(freshness, "status", "UNAVAILABLE");
    }

    String observedAt() {
        JSONObject freshness = value.optJSONObject("source_freshness");
        return text(freshness, "observed_at", "");
    }

    boolean actionsAllowed() {
        JSONObject freshness = value.optJSONObject("source_freshness");
        return freshness != null
            && "LIVE".equals(text(freshness, "status", ""))
            && freshness.optBoolean("actions_allowed", false);
    }

    JSONArray people() { return array("people"); }
    JSONArray rooms() { return array("rooms"); }
    JSONArray cameras() { return array("cameras"); }
    JSONArray appliances() { return array("appliances"); }
    JSONArray energy() { return array("energy"); }
    JSONArray activeMedia() { return array("active_media"); }
    JSONArray incidents() { return array("current_incidents"); }
    JSONArray events() { return array("recent_events"); }
    JSONArray quickActions() { return array("quick_actions"); }

    JSONObject lights() {
        JSONObject lights = value.optJSONObject("lights");
        return lights == null ? new JSONObject() : lights;
    }

    JSONObject devices() {
        JSONObject devices = value.optJSONObject("devices");
        return devices == null ? new JSONObject() : devices;
    }

    JSONObject diagnostics() {
        JSONObject diagnostics = value.optJSONObject("diagnostics");
        return diagnostics == null ? new JSONObject() : diagnostics;
    }

    JSONObject room(String areaId) {
        return find(rooms(), "area_id", areaId);
    }

    JSONObject event(String eventId) {
        return find(events(), "event_id", eventId);
    }

    JSONObject incident(String incidentId) {
        return find(incidents(), "incident_id", incidentId);
    }

    JSONObject camera(String entityId) {
        return find(cameras(), "entity_id", entityId);
    }

    JSONObject action(String actionId) {
        JSONObject action = find(quickActions(), "action_id", actionId);
        if (action != null) return action;
        JSONArray rooms = rooms();
        for (int index = 0; index < rooms.length(); index++) {
            JSONObject room = rooms.optJSONObject(index);
            if (room == null) continue;
            action = find(room.optJSONArray("quick_actions"), "action_id", actionId);
            if (action != null) return action;
        }
        return null;
    }

    private JSONArray array(String key) {
        JSONArray values = value.optJSONArray(key);
        return values == null ? new JSONArray() : values;
    }

    static JSONObject find(JSONArray values, String key, String wanted) {
        if (values == null || wanted == null) return null;
        for (int index = 0; index < values.length(); index++) {
            JSONObject item = values.optJSONObject(index);
            if (item != null && wanted.equals(text(item, key, ""))) return item;
        }
        return null;
    }

    static String text(JSONObject value, String key, String fallback) {
        if (value == null || value.isNull(key)) return fallback;
        String result = value.optString(key, "").trim();
        if (result.isBlank()) return fallback;
        String lower = result.toLowerCase(java.util.Locale.ROOT);
        return "null".equals(lower) || "none".equals(lower) || "undefined".equals(lower)
            ? fallback : result;
    }
}
