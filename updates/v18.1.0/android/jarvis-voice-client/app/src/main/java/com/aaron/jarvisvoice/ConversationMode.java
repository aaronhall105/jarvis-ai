package com.aaron.jarvisvoice;

import java.util.Locale;

public final class ConversationMode {
    public static final String LIVE = "live";
    public static final String STANDARD = "standard";

    private ConversationMode() {}

    public static String normalise(String value) {
        String mode = value == null ? "" : value.trim().toLowerCase(Locale.ROOT);
        return STANDARD.equals(mode) ? STANDARD : LIVE;
    }

    public static String label(String value) {
        return STANDARD.equals(normalise(value)) ? "Standard" : "Live";
    }

    public static String toggle(String value) {
        return STANDARD.equals(normalise(value)) ? LIVE : STANDARD;
    }
}
