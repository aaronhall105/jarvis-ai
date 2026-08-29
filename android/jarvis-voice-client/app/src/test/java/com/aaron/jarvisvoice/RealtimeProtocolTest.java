package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

public final class RealtimeProtocolTest {
    @Test public void authContainsProductVoiceSettingsAndNoApiKey() throws Exception {
        JSONObject auth = new JSONObject(RealtimeProtocol.auth(
            "mobile-token",
            "phone-1",
            "aaron",
            "Aaron",
            "cedar",
            "realtime",
            "live",
            "high",
            "mobile-chat-123"
        ));
        assertEquals("auth", auth.getString("type"));
        assertEquals("mobile-token", auth.getString("token"));
        assertEquals("aaron", auth.getString("user_id"));
        assertEquals("Aaron", auth.getString("user_name"));
        assertEquals("live", auth.getString("conversation_mode"));
        assertEquals("high", auth.getString("vad_eagerness"));
        assertEquals("mobile-chat-123", auth.getString("conversation_id"));
        assertEquals("PHONE", auth.getString("endpoint"));
        assertFalse(auth.toString().contains("OPENAI_API_KEY"));
    }

    @Test public void watchEndpointIsExplicitInAuthentication() throws Exception {
        JSONObject auth = new JSONObject(RealtimeProtocol.auth(
            "token", "watch-via-phone", "aaron", "Aaron", "cedar",
            "realtime", "live", "high", "conversation", "WATCH"
        ));
        assertEquals("WATCH", auth.getString("endpoint"));
    }

    @Test public void brainDeltaAndResponseAreParsed() throws Exception {
        RealtimeProtocol.Event delta = RealtimeProtocol.parse(
            "{\"type\":\"brain.delta\",\"text\":\"Amber is\"}"
        );
        assertEquals("brain.delta", delta.type);
        assertEquals("Amber is", delta.text);

        RealtimeProtocol.Event response = RealtimeProtocol.parse(
            "{\"type\":\"brain.response\",\"text\":\"Amber is at home\",\"success\":true,\"conversation_id\":\"mobile-chat-123\"}"
        );
        assertEquals("Amber is at home", response.text);
        assertEquals("mobile-chat-123", response.conversationId);
        assertTrue(response.success);
    }

    @Test public void readyReportsConversationMode() throws Exception {
        RealtimeProtocol.Event event = RealtimeProtocol.parse(
            "{\"type\":\"ready\",\"model\":\"gpt-realtime\",\"voice\":\"marin\",\"voice_mode\":\"realtime\",\"conversation_mode\":\"standard\",\"unified_brain\":true}"
        );
        assertEquals("standard", event.conversationMode);
        assertTrue(event.unifiedBrain);
    }

    @Test public void textMessageCanDisableSpeech() throws Exception {
        JSONObject message = new JSONObject(RealtimeProtocol.text("hello", false));
        assertEquals("text", message.getString("type"));
        assertEquals("hello", message.getString("text"));
        assertFalse(message.getBoolean("speak"));
    }

    @Test public void turnStatusRequestCarriesOriginalClientTurnId()
            throws Exception {
        JSONObject request = new JSONObject(
            RealtimeProtocol.turnStatus(73L)
        );

        assertEquals(
            "turn.status",
            request.getString("type")
        );

        assertEquals(
            73L,
            request.getLong("client_turn_id")
        );
    }

    @Test public void completedTurnStatusParsesRecoveryResponse()
            throws Exception {
        RealtimeProtocol.Event event =
            RealtimeProtocol.parse(
                "{"
                    + "\"type\":\"turn.status\","
                    + "\"client_turn_id\":73,"
                    + "\"found\":true,"
                    + "\"status\":\"completed\","
                    + "\"conversation_id\":\"chat-1\","
                    + "\"response\":{"
                    + "\"text\":\"Done.\","
                    + "\"success\":true,"
                    + "\"conversation_id\":\"chat-1\""
                    + "}"
                    + "}"
            );

        assertEquals(
            "turn.status",
            event.type
        );

        assertEquals(
            73L,
            event.clientTurnId
        );

        assertTrue(event.found);

        assertEquals(
            "completed",
            event.turnStatus
        );

        assertEquals(
            "Done.",
            event.recoveryText
        );

        assertTrue(
            event.recoverySuccess
        );

        assertEquals(
            "chat-1",
            event.recoveryConversationId
        );
    }

    @Test public void unknownTurnStatusIsExplicit()
            throws Exception {
        RealtimeProtocol.Event event =
            RealtimeProtocol.parse(
                "{"
                    + "\"type\":\"turn.status\","
                    + "\"client_turn_id\":91,"
                    + "\"found\":false,"
                    + "\"status\":\"unknown\""
                    + "}"
            );

        assertFalse(event.found);

        assertEquals(
            "unknown",
            event.turnStatus
        );
    }

    @Test public void turnAcceptedAndConflictRemainAddressable()
            throws Exception {
        RealtimeProtocol.Event accepted =
            RealtimeProtocol.parse(
                "{"
                    + "\"type\":\"turn.accepted\","
                    + "\"client_turn_id\":92,"
                    + "\"status\":\"accepted\""
                    + "}"
            );

        assertEquals(
            92L,
            accepted.clientTurnId
        );

        assertEquals(
            "accepted",
            accepted.turnStatus
        );

        RealtimeProtocol.Event conflict =
            RealtimeProtocol.parse(
                "{"
                    + "\"type\":\"turn.conflict\","
                    + "\"client_turn_id\":92,"
                    + "\"status\":\"completed\","
                    + "\"message\":\"conflict\""
                    + "}"
            );

        assertEquals(
            "turn.conflict",
            conflict.type
        );

        assertEquals(
            "conflict",
            conflict.message
        );
    }

}
