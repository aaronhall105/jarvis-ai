package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;

import org.junit.Test;

public final class ChatMessageTest {
    @Test public void legacyConstructorCreatesStableShape() {
        ChatMessage message = new ChatMessage(
            ChatMessage.USER,
            "Hello Jarvis",
            123L
        );
        assertFalse(message.id.isBlank());
        assertEquals(ChatMessage.USER, message.role);
        assertEquals("Hello Jarvis", message.text);
        assertEquals(123L, message.createdAt);
    }
}
