package com.aaron.jarvisvoice;

/**
 * Prevents a completed Core turn from releasing its durable
 * recovery record before the final user-visible response has
 * itself been durably persisted.
 */
final class TurnDeliveryFence {
    enum Action {
        CLEAR_TERMINAL,
        RECONCILE_COMPLETED
    }

    private TurnDeliveryFence() {
    }

    static Action afterTerminal(
        boolean recoveryTracked,
        boolean responseDurablyDelivered
    ) {
        if (
            recoveryTracked
                && !responseDurablyDelivered
        ) {
            return Action.RECONCILE_COMPLETED;
        }

        return Action.CLEAR_TERMINAL;
    }
}
