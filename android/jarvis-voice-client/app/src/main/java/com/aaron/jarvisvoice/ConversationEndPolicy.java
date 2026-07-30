package com.aaron.jarvisvoice;

import java.util.Locale;

public final class ConversationEndPolicy {
    private ConversationEndPolicy() {}

    public static boolean shouldEnd(String raw) {
        String value = raw == null
            ? ""
            : raw.toLowerCase(Locale.ROOT)
                .replaceAll("[^a-z0-9' ]+", " ")
                .replaceAll("\\s+", " ")
                .trim();

        if (value.isEmpty()) return false;

        value = value.replaceFirst(
            "^(okay|ok|alright|all right|right|well|please)\\s+",
            ""
        );
        value = value.replaceFirst("\\s+(please|jarvis)$", "");

        return value.equals("that is all")
            || value.equals("that's all")
            || value.equals("thats all")
            || value.equals("that is all thank you")
            || value.equals("that's all thank you")
            || value.equals("thats all thank you")
            || value.equals("that is all thanks")
            || value.equals("that's all thanks")
            || value.equals("thats all thanks")
            || value.equals("thanks")
            || value.equals("thank you")
            || value.equals("cheers")
            || value.equals("goodbye")
            || value.equals("good bye")
            || value.equals("bye")
            || value.equals("see you")
            || value.equals("see you later")
            || value.equals("stop listening")
            || value.equals("end conversation")
            || value.equals("end the conversation")
            || value.equals("stop");
    }
}
