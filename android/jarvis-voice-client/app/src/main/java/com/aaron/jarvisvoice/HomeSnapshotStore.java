package com.aaron.jarvisvoice;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONObject;

/** Principal-scoped cache containing only Core's presentation-safe Home projection. */
final class HomeSnapshotStore {
    record Snapshot(JSONObject response, String etag, long receivedAtMillis) {}

    static final String PREFS = "jarvis_home_snapshots_v1";
    private final SharedPreferences preferences;
    private final String principal;

    HomeSnapshotStore(Context context, String principal) {
        preferences = context.getApplicationContext()
            .getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        this.principal = normalise(principal);
    }

    void save(JSONObject response, String etag, long receivedAtMillis) {
        if (response == null) return;
        preferences.edit()
            .putString(key("json"), response.toString())
            .putString(key("etag"), etag == null ? "" : etag)
            .putLong(key("received"), Math.max(0L, receivedAtMillis))
            .apply();
    }

    void touch(long receivedAtMillis) {
        preferences.edit().putLong(key("received"), Math.max(0L, receivedAtMillis)).apply();
    }

    Snapshot load() {
        String raw = preferences.getString(key("json"), "");
        if (raw.isBlank()) return null;
        try {
            return new Snapshot(
                new JSONObject(raw),
                preferences.getString(key("etag"), ""),
                preferences.getLong(key("received"), 0L)
            );
        } catch (Exception ignored) {
            return null;
        }
    }

    private String key(String suffix) { return principal + ":" + suffix; }

    private static String normalise(String value) {
        return (value == null ? "" : value.trim().toLowerCase(java.util.Locale.ROOT))
            .replaceAll("[^a-z0-9_:-]+", "_");
    }
}
