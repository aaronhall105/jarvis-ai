package com.aaron.jarvisvoice;

import java.util.Arrays;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;

public final class TranscriptPolicy {
    public enum Action { IGNORE, STOP, COMMAND }

    public static final class Decision {
        public final Action action;
        public final String command;
        public final String reason;

        private Decision(Action action, String command, String reason) {
            this.action = action;
            this.command = command;
            this.reason = reason;
        }

        public static Decision ignore(String reason) {
            return new Decision(Action.IGNORE, "", reason);
        }

        public static Decision stop(String reason) {
            return new Decision(Action.STOP, "", reason);
        }

        public static Decision command(String command, String reason) {
            return new Decision(Action.COMMAND, command, reason);
        }
    }

    private static final Set<String> STOP_PHRASES = new HashSet<>(Arrays.asList(
        "stop", "stop talking", "stop listening", "be quiet", "quiet", "hush",
        "cancel", "cancel that", "never mind", "nevermind", "that's all",
        "that is all", "leave it", "forget it"
    ));

    private TranscriptPolicy() {}

    public static Decision evaluate(
            String transcript,
            String assistantSpeech,
            boolean followUpAllowed,
            boolean assistantSpeaking,
            String wakePhrase
    ) {
        String heard = normalise(transcript);
        if (heard.isEmpty()) {
            return Decision.ignore("empty");
        }

        String wake = normalise(wakePhrase);
        if (wake.isEmpty()) {
            wake = "jarvis";
        }

        String command = stripWakePrefix(heard, wake);
        boolean addressed = !command.equals(heard);

        if (assistantSpeaking && !addressed) {
            if (looksLikeSelfEcho(heard, assistantSpeech)) {
                return Decision.ignore("assistant_self_echo");
            }
            return Decision.ignore("wake_required_during_playback");
        }

        if (!assistantSpeaking && !followUpAllowed && !addressed) {
            return Decision.ignore("wake_required");
        }

        if (addressed && command.isEmpty()) {
            return Decision.ignore("wake_only");
        }

        String accepted = addressed ? command : heard;
        if (STOP_PHRASES.contains(accepted)) {
            return Decision.stop("stop_phrase");
        }

        if (accepted.split(" ").length > 45) {
            return Decision.ignore("too_long");
        }

        return Decision.command(accepted, addressed ? "addressed" : "expected_follow_up");
    }

    public static String normalise(String value) {
        if (value == null) {
            return "";
        }
        return value
            .toLowerCase(Locale.UK)
            .replace('’', '\'')
            .replaceAll("[^a-z0-9' ]", " ")
            .replaceAll("\\s+", " ")
            .trim();
    }

    private static String stripWakePrefix(String text, String wake) {
        String[] prefixes = new String[] { wake, "hey " + wake, "okay " + wake, "ok " + wake };
        for (String prefix : prefixes) {
            if (text.equals(prefix)) {
                return "";
            }
            if (text.startsWith(prefix + " ")) {
                return text.substring(prefix.length()).trim();
            }
        }
        return text;
    }

    static boolean looksLikeSelfEcho(String transcript, String assistantSpeech) {
        String heard = normalise(transcript);
        String spoken = normalise(assistantSpeech);
        if (heard.split(" ").length < 3 || spoken.split(" ").length < 3) {
            return false;
        }
        if (spoken.contains(heard) || heard.contains(spoken)) {
            return true;
        }
        Set<String> a = new HashSet<>(Arrays.asList(heard.split(" ")));
        Set<String> b = new HashSet<>(Arrays.asList(spoken.split(" ")));
        int smaller = Math.min(a.size(), b.size());
        if (smaller == 0) {
            return false;
        }
        a.retainAll(b);
        return ((double) a.size() / (double) smaller) >= 0.80;
    }
}
