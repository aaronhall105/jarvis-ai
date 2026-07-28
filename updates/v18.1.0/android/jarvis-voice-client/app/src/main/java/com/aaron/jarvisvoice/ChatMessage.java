package com.aaron.jarvisvoice;

public final class ChatMessage {
    public static final String USER = "user";
    public static final String ASSISTANT = "assistant";
    public static final String SYSTEM = "system";

    public final String role;
    public final String text;
    public final long createdAt;

    public ChatMessage(String role, String text, long createdAt) {
        this.role = role == null ? SYSTEM : role;
        this.text = text == null ? "" : text;
        this.createdAt = createdAt;
    }
}
