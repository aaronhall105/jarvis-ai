package com.aaron.jarvisvoice;

import java.util.Locale;

/** Classifies commands which cancel output and must never become a chat turn. */
public final class InterruptionCommandPolicy {
    private InterruptionCommandPolicy() {}

    public static boolean isCancellationOnly(String value) {
        String normalised = value == null
            ? ""
            : value.toLowerCase(Locale.UK)
                .replaceAll("[^a-z0-9']+", " ")
                .trim()
                .replaceAll("\\s+", " ");
        return normalised.matches(
            "^(?:jarvis )?(?:stop|stop talking|wait|cancel|quiet|be quiet|hush|hold on|hang on)(?: please)?$"
        );
    }
}
