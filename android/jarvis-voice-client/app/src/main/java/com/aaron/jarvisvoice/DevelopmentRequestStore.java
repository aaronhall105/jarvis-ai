package com.aaron.jarvisvoice;

import android.content.Context;
import android.content.SharedPreferences;
import java.util.UUID;
import org.json.JSONObject;

/** Persists submission identity before transport; reconnect never invents another request. */
final class DevelopmentRequestStore {
    private final SharedPreferences preferences;

    DevelopmentRequestStore(Context context) {
        preferences = context.getApplicationContext().getSharedPreferences(
            "jarvis_supervised_development_requests_v1", Context.MODE_PRIVATE);
    }

    synchronized JSONObject begin(String text) throws Exception {
        String objective = text == null ? "" : text.trim();
        if (objective.length() < 3 || objective.length() > 2000)
            throw new IllegalArgumentException("Describe the improvement in 3–2000 characters.");
        JSONObject current = pending();
        if (current != null) {
            if (objective.equals(current.optString("request"))) return current;
            throw new IllegalStateException("Reconcile the existing request before submitting another.");
        }
        JSONObject request = new JSONObject().put("request_id", UUID.randomUUID().toString())
            .put("request", objective);
        if (!preferences.edit().putString("pending", request.toString())
            .putString("last_request_id", request.getString("request_id")).commit())
            throw new IllegalStateException("Request identity could not be saved; nothing was submitted.");
        return request;
    }

    synchronized JSONObject pending() throws Exception {
        String stored = preferences.getString("pending", "");
        if (stored.isBlank()) return null;
        JSONObject request = new JSONObject(stored);
        requireId(request.getString("request_id"));
        if (request.getString("request").isBlank()) throw new IllegalStateException("Saved request is incomplete.");
        return request;
    }

    synchronized void acknowledge(JSONObject result) throws Exception {
        String id = result.getString("request_id");
        requireId(id);
        if (!result.optBoolean("success") || result.optInt("candidate_id", 0) < 1)
            throw new IllegalArgumentException("Development request was not confirmed.");
        JSONObject pending = pending();
        if (pending != null && !id.equals(pending.getString("request_id")))
            throw new IllegalArgumentException("Development response identity mismatch.");
        if (!preferences.edit().remove("pending").putString("last_request_id", id).commit())
            throw new IllegalStateException("Request confirmation could not be saved.");
    }

    String lastRequestId() { return preferences.getString("last_request_id", ""); }

    static void requireId(String value) {
        if (value == null || !UUID.fromString(value).toString().equals(value))
            throw new IllegalArgumentException("Invalid development request identity.");
    }
}
