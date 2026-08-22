package com.aaron.jarvisvoice;

import org.json.JSONArray;
import org.json.JSONObject;

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

    static String details(JSONObject item) {
        if (item == null) return "No details available.";
        String type = item.optString("type");
        if ("commandExecution".equals(type)) {
            String command = item.optString("command");
            String output = item.optString("aggregatedOutput", item.optString("output"));
            String exit = item.has("exitCode") ? "\n\nExit code: " + item.optInt("exitCode") : "";
            return (command.isBlank() ? "" : "$ " + command + "\n\n")
                + (output.isBlank() ? "No command output." : output) + exit;
        }
        if ("fileChange".equals(type)) {
            JSONArray changes = item.optJSONArray("changes");
            if (changes == null || changes.length() == 0) return "No file diff was supplied.";
            StringBuilder value = new StringBuilder();
            for (int index = 0; index < changes.length(); index++) {
                JSONObject change = changes.optJSONObject(index);
                if (change == null) continue;
                if (value.length() > 0) value.append("\n\n");
                value.append(change.optString("path", "Changed file"));
                String diff = change.optString("diff");
                if (!diff.isBlank()) value.append("\n").append(diff);
            }
            return value.length() == 0 ? "No file diff was supplied." : value.toString();
        }
        return item.toString();
    }
}
