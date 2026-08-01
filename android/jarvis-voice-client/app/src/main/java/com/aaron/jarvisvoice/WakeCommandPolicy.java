package com.aaron.jarvisvoice;

import java.util.Set;

public final class WakeCommandPolicy {
    private static final Set<String> SINGLE_WORD_COMMANDS = Set.of(
        "stop",
        "cancel",
        "quiet",
        "hello",
        "weather",
        "time",
        "help"
    );

    private static final Set<String> FILLER_ONLY = Set.of(
        "yes",
        "no",
        "okay",
        "ok",
        "right",
        "yeah",
        "yep",
        "uh",
        "um"
    );

    private WakeCommandPolicy() {}

    public static String commandAfterWake(
        String transcript,
        String configuredWakePhrase
    ) {
        String heard = WakePhrasePolicy.normalise(transcript);
        if (heard.isEmpty()) return "";

        for (String prefix : WakePhrasePolicy.prefixes(
            configuredWakePhrase
        )) {
            if (heard.equals(prefix)) return "";
            if (heard.startsWith(prefix + " ")) {
                heard = heard.substring(prefix.length()).trim();
                break;
            }
        }

        return isMeaningful(heard) ? heard : "";
    }

    public static boolean isMeaningful(String candidate) {
        String command = WakePhrasePolicy.normalise(candidate);
        if (command.isEmpty() || FILLER_ONLY.contains(command)) {
            return false;
        }

        String[] words = command.split(" ");
        if (words.length >= 2) return true;

        return SINGLE_WORD_COMMANDS.contains(command);
    }
}
