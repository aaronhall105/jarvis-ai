package com.aaron.jarvisvoice;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONObject;

/** App-private cache of already-redacted mobile Task Centre responses. */
final class TaskSnapshotStore {
    record Snapshot(JSONObject response, long receivedAtMillis) {}

    private static final String PREFS = "jarvis_task_snapshots_v1";
    private final SharedPreferences preferences;
    private final String principal;

    TaskSnapshotStore(Context context, String principal) {
        preferences = context.getApplicationContext()
            .getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        this.principal = normalise(principal);
    }

    void save(String filter, JSONObject response, long receivedAtMillis) {
        if (response == null) return;
        preferences.edit()
            .putString(key(filter, "json"), response.toString())
            .putLong(key(filter, "received"), Math.max(receivedAtMillis, 0L))
            .apply();
    }

    Snapshot load(String filter) {
        String raw = preferences.getString(key(filter, "json"), "");
        if (raw.isBlank()) return null;
        try {
            return new Snapshot(
                new JSONObject(raw),
                preferences.getLong(key(filter, "received"), 0L)
            );
        } catch (Exception ignored) {
            return null;
        }
    }

    private String key(String filter, String suffix) {
        return principal + ":" + normalise(filter) + ":" + suffix;
    }

    private static String normalise(String value) {
        return (value == null ? "" : value.trim().toLowerCase())
            .replaceAll("[^a-z0-9_:-]+", "_");
    }
}
