package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

public final class EmailAssistantSettingsTest {
    @Test public void settingsRoundTripKeepsCleanupSafeAndUserFacing() throws Exception {
        JSONObject source = new JSONObject()
            .put("important_email_alerts", true)
            .put("reply_alerts", true)
            .put("inbox_cleanup", false)
            .put("cleanup_dry_run", true)
            .put("importance_threshold", "important")
            .put("cleanup_mode", "trash")
            .put("cleanup_age_days", 30)
            .put("protected_senders", new JSONArray().put("@bank.example"))
            .put("status", "active")
            .put("last_gmail_check", JSONObject.NULL);

        EmailAssistantSettings settings = EmailAssistantSettings.fromJson(source);
        JSONObject encoded = settings.toJson("android-email-assistant");

        assertTrue(settings.importantAlerts);
        assertTrue(settings.replyAlerts);
        assertFalse(settings.inboxCleanup);
        assertTrue(settings.cleanupDryRun);
        assertEquals("", settings.lastGmailCheck);
        assertEquals("android-email-assistant", encoded.getString("conversation_id"));
        assertEquals("trash", encoded.getString("cleanup_mode"));
        assertEquals(30, encoded.getInt("cleanup_age_days"));
        assertEquals("@bank.example", encoded.getJSONArray("protected_senders").getString(0));
    }

    @Test public void enablingCleanupDoesNotDisableDryRun() {
        EmailAssistantSettings original = EmailAssistantSettings.fromJson(new JSONObject());
        EmailAssistantSettings enabled = original.withToggles(false, false, true);

        assertTrue(enabled.inboxCleanup);
        assertTrue(enabled.cleanupDryRun);
    }
}
