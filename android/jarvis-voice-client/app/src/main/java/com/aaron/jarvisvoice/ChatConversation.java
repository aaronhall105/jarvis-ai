package com.aaron.jarvisvoice;

public final class ChatConversation {
    public final String id;
    public final String title;
    public final String owner;
    public final long createdAt;
    public final long updatedAt;
    public final boolean pinned;
    public final String preview;
    public final int messageCount;

    public ChatConversation(
        String id,
        String title,
        String owner,
        long createdAt,
        long updatedAt,
        boolean pinned,
        String preview,
        int messageCount
    ) {
        this.id = id == null ? "" : id;
        this.title = title == null || title.isBlank()
            ? "New chat"
            : title;
        this.owner = owner == null ? "" : owner;
        this.createdAt = createdAt;
        this.updatedAt = updatedAt;
        this.pinned = pinned;
        this.preview = preview == null ? "" : preview;
        this.messageCount = Math.max(0, messageCount);
    }
}
