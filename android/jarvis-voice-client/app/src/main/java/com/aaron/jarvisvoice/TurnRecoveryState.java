package com.aaron.jarvisvoice;

/**
 * Owns one outbound text turn while its durable Core outcome
 * may still need reconciliation after transport replacement.
 *
 * Transport loss deliberately does not clear the pending turn.
 */
final class TurnRecoveryState {
    private long clientTurnId;
    private String text = "";
    private boolean speak;
    private boolean responseDelivered;
    private int statusChecks;

    void begin(
        long clientTurnId,
        String text,
        boolean speak
    ) {
        if (clientTurnId <= 0L) {
            throw new IllegalArgumentException(
                "clientTurnId must be positive"
            );
        }

        String command = text == null
            ? ""
            : text.trim();

        if (command.isEmpty()) {
            throw new IllegalArgumentException(
                "text must not be empty"
            );
        }

        this.clientTurnId =
            clientTurnId;
        this.text =
            command;
        this.speak =
            speak;
        this.responseDelivered =
            false;
        this.statusChecks =
            0;
    }

    void restore(
        long clientTurnId,
        String text,
        boolean speak,
        boolean responseDelivered
    ) {
        begin(
            clientTurnId,
            text,
            speak
        );

        this.responseDelivered =
            responseDelivered;
    }

    boolean hasPending() {
        return clientTurnId > 0L
            && !text.isEmpty();
    }

    boolean matches(
        long value
    ) {
        return hasPending()
            && value > 0L
            && value == clientTurnId;
    }

    long clientTurnId() {
        return clientTurnId;
    }

    String text() {
        return text;
    }

    boolean speak() {
        return speak;
    }

    boolean responseDelivered() {
        return responseDelivered;
    }

    void markResponseDelivered(
        long value
    ) {
        if (matches(value)) {
            responseDelivered = true;
        }
    }

    /**
     * Explicitly documents the required network semantics:
     * transport replacement preserves the logical turn.
     */
    void onTransportLost() {
        statusChecks = 0;
    }

    void resetStatusChecks() {
        statusChecks = 0;
    }

    int noteStatusCheck() {
        return ++statusChecks;
    }

    TurnRecoveryPolicy.Action action(
        long eventTurnId,
        boolean found,
        String status,
        boolean conflict
    ) {
        if (!matches(eventTurnId)) {
            return TurnRecoveryPolicy
                .Action.IGNORE;
        }

        return TurnRecoveryPolicy.action(
            found,
            status,
            conflict
        );
    }

    void clear() {
        clientTurnId = 0L;
        text = "";
        speak = false;
        responseDelivered = false;
        statusChecks = 0;
    }
}
