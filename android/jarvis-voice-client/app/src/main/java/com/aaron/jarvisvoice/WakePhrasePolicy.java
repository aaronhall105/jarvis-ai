package com.aaron.jarvisvoice;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

public final class WakePhrasePolicy {
    public static final class Decision {
        public final boolean triggered;
        public final String command;
        public final String matchedPhrase;

        private Decision(
            boolean triggered,
            String command,
            String matchedPhrase
        ) {
            this.triggered = triggered;
            this.command = command;
            this.matchedPhrase = matchedPhrase;
        }

        public static Decision ignore() {
            return new Decision(false, "", "");
        }
    }

    private WakePhrasePolicy() {}

    public static Decision evaluate(
        String transcript,
        String configuredWakePhrase
    ) {
        String heard = normalise(transcript);
        if (heard.isEmpty()) return Decision.ignore();

        for (String prefix : prefixes(configuredWakePhrase)) {
            if (heard.equals(prefix)) {
                return new Decision(true, "", prefix);
            }
            if (heard.startsWith(prefix + " ")) {
                return new Decision(
                    true,
                    heard.substring(prefix.length()).trim(),
                    prefix
                );
            }
        }
        return Decision.ignore();
    }

    static List<String> prefixes(String configuredWakePhrase) {
        String wake = normalise(configuredWakePhrase);
        if (wake.isEmpty()) wake = "hey jarvis";

        Set<String> values = new LinkedHashSet<>();

        if ("hey jarvis".equals(wake)) {
            values.add("hey jarvis");
            values.add("okay jarvis");
            values.add("ok jarvis");
            values.add("hey jervis");
            values.add("okay jervis");
            values.add("hey javis");
            return new ArrayList<>(values);
        }

        values.add(wake);
        values.add("hey " + wake);
        values.add("okay " + wake);
        values.add("ok " + wake);

        if ("jarvis".equals(wake)) {
            values.add("jervis");
            values.add("hey jervis");
            values.add("javis");
            values.add("hey javis");
        }

        return new ArrayList<>(values);
    }

    public static String normalise(String value) {
        if (value == null) return "";
        return value
            .toLowerCase(Locale.UK)
            .replace('’', '\'')
            .replaceAll("[^a-z0-9' ]", " ")
            .replaceAll("\\s+", " ")
            .trim();
    }
}
