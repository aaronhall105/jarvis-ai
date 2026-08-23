package com.aaron.jarvisvoice;

/**
 * Defines the safe response to a durable Core turn status after
 * an Android transport reconnect.
 *
 * The cardinal rule is that a side-effecting turn may only be
 * replayed when Core explicitly says it has no durable record
 * of that client_turn_id.
 */
final class TurnRecoveryPolicy {
    enum Action {
        REPLAY_UNKNOWN,
        WAIT_ACCEPTED,
        RESTORE_COMPLETED,
        FINISH_CANCELLED,
        FINISH_INTERRUPTED,
        FAIL_CONFLICT,
        IGNORE
    }

    private TurnRecoveryPolicy() {}

    static Action action(
        boolean found,
        String status,
        boolean conflict
    ) {
        if (conflict) {
            return Action.FAIL_CONFLICT;
        }

        String normalized = status == null
            ? ""
            : status.trim().toLowerCase();

        if (!found) {
            return "unknown".equals(normalized)
                || normalized.isEmpty()
                    ? Action.REPLAY_UNKNOWN
                    : Action.IGNORE;
        }

        return switch (normalized) {
            case "accepted" ->
                Action.WAIT_ACCEPTED;

            case "completed" ->
                Action.RESTORE_COMPLETED;

            case "cancelled" ->
                Action.FINISH_CANCELLED;

            case "interrupted" ->
                Action.FINISH_INTERRUPTED;

            default ->
                Action.IGNORE;
        };
    }

    static boolean mayReplay(
        boolean found,
        String status,
        boolean conflict
    ) {
        return action(
            found,
            status,
            conflict
        ) == Action.REPLAY_UNKNOWN;
    }

    static boolean isTerminal(
        Action action
    ) {
        return action
            == Action.RESTORE_COMPLETED
            || action
                == Action.FINISH_CANCELLED
            || action
                == Action.FINISH_INTERRUPTED
            || action
                == Action.FAIL_CONFLICT;
    }
}
