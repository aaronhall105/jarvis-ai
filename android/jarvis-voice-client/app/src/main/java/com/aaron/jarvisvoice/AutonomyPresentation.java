package com.aaron.jarvisvoice;

import org.json.JSONObject;

/** Factual presentation only; never infer success from a missing response. */
final class AutonomyPresentation {
    private AutonomyPresentation() {}

    static boolean developmentApprovalAvailable(JSONObject task) {
        try {
            // Reuse the exact signer validation, but never access the Keystore
            // or generate a signature merely to display the review screen.
            AutonomyControl.developmentFields(task);
            return true;
        } catch (Exception invalid) { return false; }
    }

    static String developmentStatus(JSONObject lifecycle) {
        if (lifecycle == null) return "Status unavailable";
        switch (lifecycle.optString("status")) {
            case "idle": return "No supervised task recorded";
            case "pending": return "Proposed; no owner approval requested";
            case "approval_requested": return "Waiting for signed owner approval";
            case "expired": return "Owner approval expired; no execution authorized";
            case "blocked": return "Blocked; protected reconciliation required";
            case "running": return "Admitted development task running";
            case "review_pending": return "Independent evidence verification pending";
            case "review_required": return "Verified proposal ready for owner review; not merged or deployed";
            case "failed": return "Task failed; no automatic retry";
            default: return "Protected task status unavailable";
        }
    }

    static boolean resumeAvailable(JSONObject state) {
        // This candidate contains only the Phase 1 observer. Future engineering
        // phases need an explicit release/control policy, not a UI-only toggle.
        return false;
    }

    static boolean retryHttpFailure(String path) {
        return path.equals("/observe") || path.startsWith("/development/prepare/")
            || path.startsWith("/development/activation/") || path.startsWith("/development/proposal/");
    }

    static boolean productAcknowledged(String path, JSONObject request, JSONObject result) {
        if (request == null || result == null || !result.optBoolean("ok")
            || !"accepted".equals(result.optString("category"))) return false;
        if (path.equals("/development/start"))
            return "preparation_requested".equals(result.optString("status"))
                && request.optString("request_id").equals(result.optString("request_id"));
        if (path.equals("/development/activate"))
            return "active".equals(result.optString("status"))
                && request.optString("request_id").equals(result.optString("request_id"))
                && request.optString("task_id").equals(result.optString("task_id"));
        if (path.equals("/development/publish")) {
            JSONObject receipt = result.optJSONObject("result");
            if (receipt == null || !"PR_REVIEW_REQUIRED".equals(receipt.optString("status"))
                || receipt.optInt("number") < 1
                || !receipt.optString("head_sha").matches("[a-f0-9]{40}")
                || !request.optString("base_sha").equals(receipt.optString("base_sha"))
                || !request.optString("proposal_sha256").equals(receipt.optString("proposal_sha256"))) return false;
            return ("https://github.com/aaronhall105/jarvis-ai/pull/" + receipt.optInt("number"))
                .equals(result.optString("url"));
        }
        return false;
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
