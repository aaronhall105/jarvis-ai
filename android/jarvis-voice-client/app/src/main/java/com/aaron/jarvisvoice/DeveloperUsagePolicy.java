package com.aaron.jarvisvoice;

final class DeveloperUsagePolicy {
    private DeveloperUsagePolicy() { }

    static int clampPercent(int value) {
        return Math.max(0, Math.min(100, value));
    }

    static String windowLabel(long minutes) {
        if (minutes >= 7 * 24 * 60) return "weekly";
        if (minutes >= 24 * 60 && minutes % (24 * 60) == 0) {
            return (minutes / (24 * 60)) + "d";
        }
        if (minutes >= 60 && minutes % 60 == 0) return (minutes / 60) + "h";
        return minutes > 0 ? minutes + "m" : "current";
    }
}
