package com.aaron.jarvisvoice;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/** User-facing, principal-scoped Email Assistant settings returned by Jarvis Core. */
public final class EmailAssistantSettings {
    public final boolean importantAlerts;
    public final boolean replyAlerts;
    public final boolean inboxCleanup;
    public final boolean cleanupDryRun;
    public final String importanceThreshold;
    public final String cleanupMode;
    public final int cleanupAgeDays;
    public final List<String> protectedSenders;
    public final String status;
    public final String lastGmailCheck;
    public final String lastCleanup;
    public final String nextCleanup;
    public final boolean providerNeedsAttention;

    EmailAssistantSettings(
        boolean importantAlerts,
        boolean replyAlerts,
        boolean inboxCleanup,
        boolean cleanupDryRun,
        String importanceThreshold,
        String cleanupMode,
        int cleanupAgeDays,
        List<String> protectedSenders,
        String status,
        String lastGmailCheck,
        String lastCleanup,
        String nextCleanup,
        boolean providerNeedsAttention
    ) {
        this.importantAlerts = importantAlerts;
        this.replyAlerts = replyAlerts;
        this.inboxCleanup = inboxCleanup;
        this.cleanupDryRun = cleanupDryRun;
        this.importanceThreshold = importanceThreshold;
        this.cleanupMode = cleanupMode;
        this.cleanupAgeDays = cleanupAgeDays;
        this.protectedSenders = List.copyOf(protectedSenders);
        this.status = status;
        this.lastGmailCheck = lastGmailCheck;
        this.lastCleanup = lastCleanup;
        this.nextCleanup = nextCleanup;
        this.providerNeedsAttention = providerNeedsAttention;
    }

    static EmailAssistantSettings fromJson(JSONObject value) {
        List<String> protectedSenders = new ArrayList<>();
        JSONArray values = value.optJSONArray("protected_senders");
        if (values != null) {
            for (int index = 0; index < values.length(); index++) {
                String sender = values.optString(index, "").trim();
                if (!sender.isBlank()) protectedSenders.add(sender);
            }
        }
        return new EmailAssistantSettings(
            value.optBoolean("important_email_alerts", false),
            value.optBoolean("reply_alerts", false),
            value.optBoolean("inbox_cleanup", false),
            value.optBoolean("cleanup_dry_run", true),
            value.optString("importance_threshold", "important"),
            value.optString("cleanup_mode", "trash"),
            Math.max(1, value.optInt("cleanup_age_days", 30)),
            protectedSenders,
            value.optString("status", "paused"),
            optional(value, "last_gmail_check"),
            optional(value, "last_cleanup"),
            optional(value, "next_cleanup"),
            !optional(value, "provider_error").isBlank()
        );
    }

    JSONObject toJson(String conversationId) throws JSONException {
        JSONObject value = new JSONObject();
        value.put("conversation_id", conversationId);
        value.put("important_email_alerts", importantAlerts);
        value.put("reply_alerts", replyAlerts);
        value.put("inbox_cleanup", inboxCleanup);
        value.put("cleanup_dry_run", cleanupDryRun);
        value.put("importance_threshold", importanceThreshold);
        value.put("cleanup_mode", cleanupMode);
        value.put("cleanup_age_days", cleanupAgeDays);
        value.put("protected_senders", new JSONArray(protectedSenders));
        return value;
    }

    EmailAssistantSettings withToggles(boolean important, boolean replies, boolean cleanup) {
        return new EmailAssistantSettings(
            important,
            replies,
            cleanup,
            cleanupDryRun,
            importanceThreshold,
            cleanupMode,
            cleanupAgeDays,
            protectedSenders,
            status,
            lastGmailCheck,
            lastCleanup,
            nextCleanup,
            providerNeedsAttention
        );
    }

    EmailAssistantSettings withOptions(String threshold, String mode, int ageDays) {
        return new EmailAssistantSettings(
            importantAlerts,
            replyAlerts,
            inboxCleanup,
            cleanupDryRun,
            threshold,
            mode,
            ageDays,
            protectedSenders,
            status,
            lastGmailCheck,
            lastCleanup,
            nextCleanup,
            providerNeedsAttention
        );
    }

    private static String optional(JSONObject value, String key) {
        Object raw = value.opt(key);
        return raw == null || raw == JSONObject.NULL ? "" : String.valueOf(raw);
    }
}
