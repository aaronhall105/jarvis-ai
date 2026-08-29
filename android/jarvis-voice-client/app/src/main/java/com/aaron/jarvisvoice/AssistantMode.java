package com.aaron.jarvisvoice;

public enum AssistantMode {
    JARVIS,
    DEVELOPER;

    static AssistantMode from(String value) {
        try { return valueOf(value); }
        catch (Exception ignored) { return JARVIS; }
    }
}
