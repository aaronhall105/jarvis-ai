package com.aaron.jarvisvoice;

import java.util.Locale;

public final class ExpectedTransitionPolicy {
    private ExpectedTransitionPolicy() {}

    public static boolean isExpected(
        String from,
        String to,
        String reason
    ) {
        String source = safe(from);
        String target = safe(to);
        String detail = safe(reason);
        String combined = source + " " + target + " " + detail;

        if (
            combined.contains("barge")
                || combined.contains("interrupt")
                || combined.contains("reconnect")
                || combined.contains("network")
                || combined.contains("endpoint")
                || combined.contains("tailscale")
                || combined.contains("playback")
                || combined.contains("wake")
                || combined.contains("restore")
                || combined.contains("recover")
                || combined.contains("cancel")
                || combined.contains("service restart")
                || combined.contains("audio route")
                || combined.contains("audio focus")
        ) {
            return true;
        }

        return (
            ("speaking".equals(source) || "thinking".equals(source))
                && "listening".equals(target)
        ) || (
            ("offline".equals(source)
                || "disconnected".equals(source)
                || "error".equals(source))
                && (
                    "connecting".equals(target)
                        || "listening".equals(target)
                )
        );
    }

    private static String safe(String value) {
        return value == null
            ? ""
            : value.trim().toLowerCase(Locale.ROOT);
    }
}
