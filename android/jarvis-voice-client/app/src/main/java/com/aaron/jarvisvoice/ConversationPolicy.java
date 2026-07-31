package com.aaron.jarvisvoice;

import java.util.Locale;

public final class ConversationPolicy {
    public static final String TODAY = "Today";
    public static final String PREVIOUS_7_DAYS = "Previous 7 days";
    public static final String OLDER = "Older";

    private ConversationPolicy() {}

    public static String titleFromText(String value) {
        String text = clean(value);
        if (text.isBlank()) return "New chat";
        int newline = text.indexOf('\n');
        if (newline >= 0) text = text.substring(0, newline).trim();
        if (text.length() <= 48) return text;
        return text.substring(0, 47).trim() + "…";
    }

    public static boolean sameOwner(String first, String second) {
        return normaliseOwner(first).equals(normaliseOwner(second));
    }

    public static boolean matches(
        ChatConversation conversation,
        String query
    ) {
        String needle = clean(query).toLowerCase(Locale.ROOT);
        if (needle.isBlank()) return true;
        return (
            clean(conversation.title)
                + " "
                + clean(conversation.preview)
                + " "
                + clean(conversation.owner)
        ).toLowerCase(Locale.ROOT).contains(needle);
    }

    public static String section(
        long updatedAt,
        long startOfToday,
        long sevenDaysAgo
    ) {
        if (updatedAt >= startOfToday) return TODAY;
        if (updatedAt >= sevenDaysAgo) return PREVIOUS_7_DAYS;
        return OLDER;
    }

    public static String normaliseOwner(String value) {
        String cleaned = clean(value).toLowerCase(Locale.ROOT);
        return cleaned.isBlank() ? "user" : cleaned;
    }

    public static String clean(String value) {
        return value == null ? "" : value.trim();
    }
}
