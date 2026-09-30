package com.aaron.jarvisvoice;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/** Redacted mobile projection of one durable Jarvis task. */
public final class TaskItem {
    public final String taskId;
    public final String taskType;
    public final String title;
    public final String summary;
    public final String status;
    public final String underlyingStatus;
    public final String conversationId;
    public final String openChatConversationId;
    public final String currentStep;
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
    public final List<JSONObject> plannedSteps;
    public final List<JSONObject> timeline;
    public final List<String> providers;

    private TaskItem(JSONObject value) {
        taskId = value.optString("task_id", "");
        taskType = value.optString("task_type", "");
        title = value.optString("title", "Jarvis task");
        summary = value.optString("summary", "");
        status = value.optString("status", "WAITING_FOR_JARVIS");
        underlyingStatus = value.optString("underlying_status", "");
        conversationId = value.optString("conversation_id", "");
        openChatConversationId = value.optString("open_chat_conversation_id", "");
        currentStep = value.optString("current_step", "");
        nextStep = value.optString("next_step", "");
        waitingReason = value.optString("waiting_reason", "");
        errorSummary = value.optString("error_summary", "");
        resultSummary = value.optString("result_summary", "");
        createdAt = value.optString("created_at", "");
        startedAt = value.optString("started_at", "");
        updatedAt = value.optString("updated_at", "");
        completedAt = value.optString("completed_at", "");
        scheduledAt = value.optString("scheduled_at", "");
        progressCurrent = nullableInt(value, "progress_current");
        progressTotal = nullableInt(value, "progress_total");
        progressUnit = value.optString("progress_unit", "");
        currentStepIndex = nullableInt(value, "current_step_index");
        stepCount = nullableInt(value, "step_count");
        requiresUserAction = value.optBoolean("requires_user_action", false);
        userActionType = value.optString("user_action_type", "");
        notificationOnCompletion = value.optBoolean("notification_on_completion", false);
        notificationOnFailure = value.optBoolean("notification_on_failure", false);
        completionNotificationState = value.optString(
            "completion_notification_state", "not_requested"
        );
        failureNotificationState = value.optString(
            "failure_notification_state", "not_requested"
        );
        notificationDeliveredAt = value.optString("notification_delivered_at", "");
        notificationDeliveryMessage = value.optString("notification_delivery_message", "");
        canCancel = value.optBoolean("can_cancel", false);
        canPause = value.optBoolean("can_pause", false);
        canResume = value.optBoolean("can_resume", false);
        canRetry = value.optBoolean("can_retry", false);
        canSteer = value.optBoolean("can_steer", false);
        canReschedule = value.optBoolean("can_reschedule", false);
        plannedSteps = objects(value.optJSONArray("planned_steps"));
        timeline = objects(value.optJSONArray("timeline"));
        providers = strings(value.optJSONArray("providers"));
    }

    public static TaskItem fromJson(JSONObject value) {
        return new TaskItem(value == null ? new JSONObject() : value);
    }

    public String statusLabel() {
        return switch (status) {
            case "WAITING_FOR_JARVIS" -> "Waiting for Jarvis";
            case "WAITING_FOR_YOU" -> "Waiting for you";
            case "SCHEDULED" -> "Scheduled";
            case "COMPLETED" -> "Completed";
            case "PARTIAL" -> "Partially completed";
            case "FAILED" -> "Failed";
            case "CANCELLED" -> "Cancelled";
            case "PLANNING" -> "Planning";
            case "PAUSED" -> "Paused";
            default -> "Running";
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
        if (progressCurrent != null && progressTotal != null && progressTotal > 0) {
            String unit = progressUnit.isBlank() ? "items" : progressUnit;
            return String.format("%,d / %,d %s", progressCurrent, progressTotal, unit);
        }
        if (currentStepIndex != null && stepCount != null && stepCount > 0) {
            return "Step " + currentStepIndex + " of " + stepCount;
        }
        return "";
    }

    public boolean isActive() {
        return switch (status) {
            case "PLANNING", "RUNNING", "WAITING_FOR_JARVIS", "WAITING_FOR_YOU", "SCHEDULED", "PAUSED" -> true;
            default -> false;
        };
    }

    private static Integer nullableInt(JSONObject value, String key) {
        return value.isNull(key) || !value.has(key) ? null : value.optInt(key);
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
