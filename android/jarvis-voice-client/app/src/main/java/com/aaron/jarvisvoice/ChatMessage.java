package com.aaron.jarvisvoice;

import java.util.UUID;

public final class ChatMessage {
    public static final String USER = "user";
    public static final String ASSISTANT = "assistant";
    public static final String SYSTEM = "system";

    public final String id;
    public final String role;
    public final String text;
    public final long createdAt;

    public ChatMessage(String role, String text, long createdAt) {
        this(
            "message-" + UUID.randomUUID(),
            role,
            text,
            createdAt
        );
    }

    public ChatMessage(
        String id,
        String role,
        String text,
        long createdAt
    ) {
        this.id = id == null || id.isBlank()
            ? "message-" + UUID.randomUUID()
            : id;
        this.role = role == null ? SYSTEM : role;
        this.text = text == null ? "" : text;
        this.createdAt = createdAt;
    }
}
