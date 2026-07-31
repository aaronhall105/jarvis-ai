package com.aaron.jarvisvoice;

public final class ProactiveUiPolicy {
    private ProactiveUiPolicy() {}

    public static String importanceLabel(int score) {
        if (score >= 95) return "Critical";
        if (score >= 85) return "High";
        if (score >= 70) return "Useful";
        return "Activity";
    }

    public static boolean sensitive(String entityId) {
        String value = entityId == null
            ? ""
            : entityId.trim().toLowerCase();
        return value.startsWith("lock.")
            || value.startsWith("alarm_control_panel.")
            || value.startsWith("cover.")
            || value.startsWith("siren.");
    }

    public static boolean mayTurnOff(ProactiveEvent event) {
        return event != null
            && event.hasAction("turn_off")
            && !sensitive(event.entityId);
    }
}
