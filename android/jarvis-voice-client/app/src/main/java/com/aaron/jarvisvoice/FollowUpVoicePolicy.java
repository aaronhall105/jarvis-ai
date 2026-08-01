package com.aaron.jarvisvoice;

import java.util.Locale;
import java.util.Set;

public final class FollowUpVoicePolicy {
    private static final Set<String> IMMEDIATE_COMMANDS =
        Set.of(
            "stop",
            "wait",
            "cancel",
            "quiet",
            "no",
            "hold on",
            "hang on"
        );

    private FollowUpVoicePolicy() {}

    public static boolean hasExplicitWake(String value) {
        String text = normalise(value);
        return text.equals("jarvis")
            || text.startsWith("jarvis ");
    }

    public static boolean isImmediateInterrupt(
        String value
    ) {
        String text = stripWakePrefix(value);
        return IMMEDIATE_COMMANDS.contains(text);
    }

    public static String stripWakePrefix(String value) {
        String text = normalise(value);
        if (text.equals("jarvis")) {
            return "";
        }
        if (text.startsWith("jarvis ")) {
            return text.substring(7).trim();
        }
        return text;
    }

    public static boolean acceptFollowUp(
        String value,
        float confidence,
        boolean withinOwnerWindow,
        boolean privateRoute
    ) {
        String text = stripWakePrefix(value);
        if (text.isEmpty()) {
            return false;
        }

        if (
            hasExplicitWake(value)
                || isImmediateInterrupt(value)
        ) {
            return true;
        }

        if (!withinOwnerWindow) {
            return false;
        }

        int words = text.split(" ").length;

        if (privateRoute) {
            return words >= 1;
        }

        if (confidence >= 0.0f) {
            return confidence >= 0.48f
                && words >= 2;
        }

        return words >= 3;
    }

    private static String normalise(String value) {
        if (value == null) {
            return "";
        }
        return value
            .toLowerCase(Locale.UK)
            .replaceAll("[^a-z0-9' ]", " ")
            .replaceAll("\\s+", " ")
            .trim();
    }
}
