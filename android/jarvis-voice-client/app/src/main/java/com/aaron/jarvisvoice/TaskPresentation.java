package com.aaron.jarvisvoice;

/** Stable client-side product naming for Task Centre items with durable internal IDs. */
final class TaskPresentation {
    private static final String SMART_INBOX_ID = "important_only:inbox";

    private TaskPresentation() {}

    static String title(TaskItem item) {
        return SMART_INBOX_ID.equals(item.taskId) ? "Smart Inbox" : item.title;
    }

    static String subtitle(TaskItem item) {
        return SMART_INBOX_ID.equals(item.taskId)
            ? "Keeps your inbox focused automatically"
            : item.activityText();
    }

    static String activity(TaskItem item) {
        if (!SMART_INBOX_ID.equals(item.taskId)) return "";
        if (!"MONITORING".equals(item.status)) return item.activityText();
        java.util.List<String> providers = new java.util.ArrayList<>();
        for (org.json.JSONObject subtask : item.subtasks) {
            String title = TaskItem.optionalString(subtask, "title", "");
            if (!title.isBlank()) providers.add(title);
        }
        if (providers.isEmpty()) return "Monitoring new mail";
        return "Monitoring " + String.join(" and ", providers);
    }

    static String policyLabel(TaskItem item) {
        String label = TaskItem.optionalString(item.metadata, "policy_label", "");
        if (SMART_INBOX_ID.equals(item.taskId) && !label.isBlank()) return "Smart Inbox active";
        return label;
    }

    static String backlogSummary(TaskItem item) {
        if (!SMART_INBOX_ID.equals(item.taskId)) return item.backlogSummary();
        if (item.backlog.isNull("reviewed_count") || item.backlog.isNull("moved_count")) {
            return item.backlogSummary();
        }
        return String.format(
            "%,d reviewed · %,d removed",
            item.backlog.optLong("reviewed_count", 0L),
            item.backlog.optLong("moved_count", 0L)
        );
    }
}
