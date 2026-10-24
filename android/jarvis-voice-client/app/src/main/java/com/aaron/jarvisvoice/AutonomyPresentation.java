package com.aaron.jarvisvoice;

import org.json.JSONObject;

/** Factual presentation only; never infer success from a missing response. */
final class AutonomyPresentation {
    private AutonomyPresentation() {}

    static boolean resumeAvailable(JSONObject state) {
        // This candidate contains only the Phase 1 observer. Future engineering
        // phases need an explicit release/control policy, not a UI-only toggle.
        return false;
    }

    static boolean retryHttpFailure(String path) {
        return !path.equals("/control");
    }

    static String status(JSONObject state) {
        String value = state.optString("status");
        if (value.equals("STOPPED") || value.equals("PAUSED")
                || value.equals("NEEDS ATTENTION")) return value;
        if (value.equals("ACTIVE") && !state.optString("phase").equals("observe_only"))
            return value;
        return "NEEDS ATTENTION";
    }

    static String currentTitle(JSONObject state) {
        JSONObject current = state.optJSONObject("current");
        return current == null ? "Nothing right now." : current.optString("title", "Checking evidence");
    }

    static String progress(JSONObject state) {
        JSONObject current = state.optJSONObject("current");
        String value = current == null ? "Thinking" : current.optString("progress", "Thinking");
        if (state.optString("phase").equals("observe_only"))
            return value.equals("Researching") ? value : "Thinking";
        switch (value) {
            case "Thinking": case "Researching": case "Building": case "Testing":
            case "Checking": case "Updating Jarvis": case "Watching results": case "Finished":
                return value;
            default: return "Checking";
        }
    }

    static String capability(JSONObject item) {
        if (!item.optBoolean("verified", false)) return "Not yet verified";
        String state = item.optString("state");
        if (state.equals("Available") || state.equals("Setup required")) return state;
        return "Temporarily unavailable";
    }
}
