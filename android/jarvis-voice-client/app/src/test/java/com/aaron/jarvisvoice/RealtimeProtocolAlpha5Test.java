package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class RealtimeProtocolAlpha5Test {
    @Test public void parsesSessionContext() throws Exception {
        RealtimeProtocol.Event event = RealtimeProtocol.parse(
            "{"
                + "\"type\":\"session.context\","
                + "\"conversation_id\":\"mobile-chat-123\","
                + "\"user_name\":\"Aaron\","
                + "\"message_count\":14"
                + "}"
        );

        assertEquals("session.context", event.type);
        assertEquals("mobile-chat-123", event.conversationId);
        assertEquals("Aaron", event.userName);
        assertEquals(14, event.messageCount);
    }

    @Test public void parsesToolCompletion() throws Exception {
        RealtimeProtocol.Event event = RealtimeProtocol.parse(
            "{"
                + "\"type\":\"tool.completed\","
                + "\"tool\":\"control_device\","
                + "\"success\":true,"
                + "\"message\":\"Living room light is now off.\""
                + "}"
        );

        assertEquals("control_device", event.tool);
        assertTrue(event.success);
        assertEquals(
            "Living room light is now off.",
            event.message
        );
    }

    @Test public void parsesTurnSummary() throws Exception {
        RealtimeProtocol.Event event = RealtimeProtocol.parse(
            "{"
                + "\"type\":\"turn.summary\","
                + "\"tool_called\":true,"
                + "\"memory_used\":false,"
                + "\"message_count\":8"
                + "}"
        );

        assertTrue(event.toolCalled);
        assertFalse(event.memoryUsed);
        assertEquals(8, event.messageCount);
    }
}
