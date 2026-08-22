package com.aaron.jarvisvoice;

final class DeveloperActivityPolicy {
    private DeveloperActivityPolicy() { }

    static String title(String type) {
        return switch (type == null ? "" : type) {
            case "commandExecution" -> "Command";
            case "fileChange" -> "Files changed";
            case "mcpToolCall" -> "Tool activity";
            case "webSearch" -> "Web search";
            default -> "Developer activity";
        };
    }

    static String status(String state, boolean completed) {
        return switch (state == null ? "" : state) {
            case "completed" -> "Completed";
            case "failed" -> "Failed";
            default -> completed ? "Completed" : "Running…";
        };
    }
}
