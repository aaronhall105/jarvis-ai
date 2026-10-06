package com.aaron.jarvisvoice;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/** Redacted mobile projection of one durable Jarvis task. */
public final class TaskItem {
    public final String taskId;
    public final String source;
    public final String sourceTaskId;
    public final String taskType;
    public final String title;
    public final String summary;
    public final String status;
    public final String underlyingStatus;
    public final String conversationId;
    public final String openChatConversationId;
    public final String currentStep;
    public final String activityTimeLabel;
    public final String workMode;
    public final String phase;
    public final String nextStep;
    public final String waitingReason;
    public final String errorSummary;
    public final String resultSummary;
    public final String createdAt;
    public final String startedAt;
    public final String updatedAt;
    public final String completedAt;
    public final String scheduledAt;
    public final Integer progressCurrent;
    public final Integer progressTotal;
    public final String progressUnit;
    public final String progressMode;
    public final Double progressFraction;
    public final Integer progressPercent;
    public final Integer progressRemaining;
    public final Long etaSeconds;
    public final String etaQuality;
    public final Integer currentStepIndex;
    public final Integer stepCount;
    public final boolean requiresUserAction;
    public final String userActionType;
    public final boolean notificationOnCompletion;
    public final boolean notificationOnFailure;
    public final String completionNotificationState;
    public final String failureNotificationState;
    public final String notificationDeliveredAt;
    public final String notificationDeliveryMessage;
    public final boolean canCancel;
    public final boolean canPause;
    public final boolean canResume;
    public final boolean canRetry;
    public final boolean canSteer;
    public final boolean canReschedule;
    public final boolean canConfirm;
    public final boolean canDecline;
    public final List<JSONObject> plannedSteps;
    public final List<JSONObject> timeline;
    public final List<JSONObject> metrics;
    public final List<JSONObject> subtasks;
    public final List<String> providers;
    public final List<String> capabilities;
    public final JSONObject backlog;
    public final JSONObject metadata;

    private TaskItem(JSONObject value) {
        taskId = optionalString(value, "task_id", "");
        source = optionalString(value, "source", "");
        sourceTaskId = optionalString(value, "source_task_id", "");
        taskType = optionalString(value, "task_type", "");
        title = optionalString(value, "title", "Jarvis task");
        summary = optionalString(value, "summary", "");
        status = optionalString(value, "status", "WAITING_FOR_JARVIS");
        underlyingStatus = optionalString(value, "underlying_status", "");
        conversationId = optionalString(value, "conversation_id", "");
        openChatConversationId = optionalString(value, "open_chat_conversation_id", "");
        currentStep = optionalString(value, "current_step", "");
        activityTimeLabel = optionalString(value, "activity_time_label", "Last task activity");
        workMode = optionalString(value, "work_mode", "BOUNDED");
        phase = optionalString(value, "phase", "");
        nextStep = optionalString(value, "next_step", "");
        waitingReason = optionalString(value, "waiting_reason", "");
        errorSummary = optionalString(value, "error_summary", "");
        resultSummary = optionalString(value, "result_summary", "");
        createdAt = optionalString(value, "created_at", "");
        startedAt = optionalString(value, "started_at", "");
        updatedAt = optionalString(value, "updated_at", "");
        completedAt = optionalString(value, "completed_at", "");
        scheduledAt = optionalString(value, "scheduled_at", "");
        JSONObject progress = value.optJSONObject("progress");
        progressCurrent = nullableInt(progress, "current", nullableInt(value, "progress_current"));
        progressTotal = nullableInt(progress, "total", nullableInt(value, "progress_total"));
        progressUnit = progress == null
            ? optionalString(value, "progress_unit", "")
            : optionalString(progress, "unit", optionalString(value, "progress_unit", ""));
        progressMode = progress == null
            ? progressTotal != null && progressTotal > 0 ? "DETERMINATE" : "INDETERMINATE"
            : optionalString(progress, "mode", "INDETERMINATE");
        progressFraction = nullableDouble(progress, "fraction");
        progressPercent = nullableInt(progress, "percent");
        progressRemaining = nullableInt(progress, "remaining");
        JSONObject timing = value.optJSONObject("timing");
        etaSeconds = nullableLong(timing, "eta_seconds");
        etaQuality = optionalString(timing, "eta_quality", "unavailable");
        currentStepIndex = nullableInt(value, "current_step_index");
        stepCount = nullableInt(value, "step_count");
        requiresUserAction = value.optBoolean("requires_user_action", false);
        userActionType = optionalString(value, "user_action_type", "");
        notificationOnCompletion = value.optBoolean("notification_on_completion", false);
        notificationOnFailure = value.optBoolean("notification_on_failure", false);
        completionNotificationState = value.optString(
            "completion_notification_state", "not_requested"
        );
        failureNotificationState = value.optString(
            "failure_notification_state", "not_requested"
        );
        notificationDeliveredAt = optionalString(value, "notification_delivered_at", "");
        notificationDeliveryMessage = optionalString(value, "notification_delivery_message", "");
        canCancel = value.optBoolean("can_cancel", false);
        canPause = value.optBoolean("can_pause", false);
        canResume = value.optBoolean("can_resume", false);
        canRetry = value.optBoolean("can_retry", false);
        canSteer = value.optBoolean("can_steer", false);
        canReschedule = value.optBoolean("can_reschedule", false);
        canConfirm = value.has("can_confirm")
            ? value.optBoolean("can_confirm", false)
            : requiresUserAction && "confirmation".equals(userActionType);
        canDecline = value.has("can_decline")
            ? value.optBoolean("can_decline", false)
            : requiresUserAction && "confirmation".equals(userActionType);
        plannedSteps = objects(value.optJSONArray("planned_steps"));
        timeline = objects(value.optJSONArray("timeline"));
        metrics = objects(value.optJSONArray("metrics"));
        subtasks = objects(value.optJSONArray("subtasks"));
        providers = strings(value.optJSONArray("providers"));
        capabilities = strings(value.optJSONArray("capabilities"));
        JSONObject backlogValue = value.optJSONObject("backlog");
        backlog = backlogValue == null ? new JSONObject() : backlogValue;
        JSONObject metadataValue = value.optJSONObject("metadata");
        metadata = metadataValue == null ? new JSONObject() : metadataValue;
    }

    public static TaskItem fromJson(JSONObject value) {
        return new TaskItem(value == null ? new JSONObject() : value);
    }

    public String statusLabel() {
        return switch (status) {
            case "WAITING_FOR_JARVIS" -> "Waiting for service";
            case "WAITING_FOR_YOU" -> "Waiting for you";
            case "SCHEDULED" -> "Scheduled";
            case "COMPLETED" -> "Completed";
            case "PARTIAL", "FAILED" -> "Needs attention";
            case "CANCELLED" -> "Cancelled";
            case "PLANNING" -> "Planning";
            case "PAUSED" -> "Paused";
            case "MONITORING" -> "Monitoring";
            case "RUNNING" -> "Running";
            default -> "Waiting for Jarvis";
        };
    }

    public String activityText() {
        if (!currentStep.isBlank()) return currentStep;
        if (!waitingReason.isBlank()) return waitingReason;
        if (!summary.isBlank()) return summary;
        if (!resultSummary.isBlank()) return resultSummary;
        return statusLabel();
    }

    public String progressText() {
        if ("NONE".equals(progressMode)) return "";
        if (progressCurrent != null && progressTotal != null && progressTotal > 0) {
            String action = "messages".equals(progressUnit) ? " reviewed" : " " + unitLabel();
            return String.format("%,d of %,d%s", progressCurrent, progressTotal, action);
        }
        if (progressCurrent != null) {
            String action = "messages".equals(progressUnit) ? " reviewed" : " " + unitLabel();
            return String.format("%,d%s", progressCurrent, action);
        }
        if (currentStepIndex != null && stepCount != null && stepCount > 0) {
            return "Step " + currentStepIndex + " of " + stepCount;
        }
        return "";
    }

    public String percentText() {
        if (!"DETERMINATE".equals(progressMode) || progressPercent == null) return "";
        return Math.max(0, Math.min(progressPercent, 100)) + "% complete";
    }

    public String remainingText() {
        if (progressRemaining == null) return "";
        return String.format("%,d remaining", Math.max(progressRemaining, 0));
    }

    public String etaText() {
        if (!"RUNNING".equals(status) || progressRemaining == null || progressRemaining <= 0) return "";
        if (etaSeconds == null || etaSeconds <= 0 || "unavailable".equals(etaQuality)) {
            return "Calculating estimate…";
        }
        long seconds = etaSeconds;
        if (seconds < 60) return "Less than a minute";
        long minutes = Math.max(1, Math.round(seconds / 60.0));
        if (minutes < 60) return "About " + minutes + (minutes == 1 ? " minute" : " minutes");
        long hours = Math.max(1, Math.round(minutes / 60.0));
        return "About " + hours + (hours == 1 ? " hour" : " hours");
    }

    public long metricValue(String key) {
        for (JSONObject metric : metrics) {
            if (key.equals(optionalString(metric, "key", ""))) return metric.optLong("value", 0L);
        }
        return 0L;
    }

    public String primaryMetricText() {
        for (JSONObject metric : metrics) {
            if (!metric.optBoolean("primary", false)) continue;
            String label = optionalString(metric, "label", "");
            if (label.isBlank() || metric.isNull("value")) continue;
            return String.format("%,d %s", metric.optLong("value", 0L), lowerFirst(label));
        }
        return "";
    }

    public String backlogTitle() {
        return optionalString(backlog, "title", "");
    }

    public String backlogSummary() {
        return optionalString(backlog, "summary", "");
    }

    public String backlogStatusLabel() {
        String value = optionalString(backlog, "status", "");
        return switch (value) {
            case "COMPLETED" -> "Complete";
            case "IN_PROGRESS" -> "In progress";
            default -> "";
        };
    }

    public boolean isActive() {
        return switch (status) {
            case "PLANNING", "RUNNING", "MONITORING", "WAITING_FOR_JARVIS", "WAITING_FOR_YOU", "SCHEDULED", "PAUSED" -> true;
            default -> false;
        };
    }

    private static Integer nullableInt(JSONObject value, String key) {
        return nullableInt(value, key, null);
    }

    private static Integer nullableInt(JSONObject value, String key, Integer fallback) {
        if (value == null || value.isNull(key) || !value.has(key)) return fallback;
        return Integer.valueOf(value.optInt(key));
    }

    private static Long nullableLong(JSONObject value, String key) {
        return value == null || value.isNull(key) || !value.has(key) ? null : value.optLong(key);
    }

    private static Double nullableDouble(JSONObject value, String key) {
        return value == null || value.isNull(key) || !value.has(key) ? null : value.optDouble(key);
    }

    static String optionalString(JSONObject value, String key, String fallback) {
        if (value == null || !value.has(key) || value.isNull(key)) return fallback;
        String candidate = value.optString(key, "").trim();
        if (candidate.isBlank()
            || "null".equalsIgnoreCase(candidate)
            || "none".equalsIgnoreCase(candidate)
            || "{}".equals(candidate)
            || "[]".equals(candidate)) return fallback;
        return candidate;
    }

    private String unitLabel() {
        return progressUnit.isBlank() ? "items" : progressUnit;
    }

    private static String lowerFirst(String value) {
        if (value == null || value.isBlank()) return "";
        return Character.toLowerCase(value.charAt(0)) + value.substring(1);
    }

    private static List<JSONObject> objects(JSONArray values) {
        List<JSONObject> output = new ArrayList<>();
        if (values == null) return output;
        for (int index = 0; index < values.length(); index++) {
            JSONObject item = values.optJSONObject(index);
            if (item != null) output.add(item);
        }
        return output;
    }

    private static List<String> strings(JSONArray values) {
        List<String> output = new ArrayList<>();
        if (values == null) return output;
        for (int index = 0; index < values.length(); index++) {
            String item = values.optString(index, "");
            if (!item.isBlank()) output.add(item);
        }
        return output;
    }
}
