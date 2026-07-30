package com.aaron.jarvisvoice;

import android.content.Context;

import java.util.EnumSet;

public final class VoiceFoundationStateMachine {
    public enum State {
        OFFLINE_WAKE,
        OPENING_SESSION,
        LISTENING,
        PROCESSING,
        SPEAKING,
        INTERRUPTING,
        CLOSING,
        RECOVERING
    }

    public enum MicrophoneOwner {
        NONE,
        OFFLINE_WAKE,
        STANDARD_RECOGNITION,
        LIVE_AUDIO
    }

    private final VoiceDiagnosticsStore diagnostics;

    private State state = State.RECOVERING;
    private MicrophoneOwner owner = MicrophoneOwner.NONE;

    public VoiceFoundationStateMachine(Context context) {
        diagnostics = new VoiceDiagnosticsStore(context);
        transition(
            State.RECOVERING,
            MicrophoneOwner.NONE,
            "voice service created"
        );
    }

    public synchronized State state() {
        return state;
    }

    public synchronized MicrophoneOwner microphoneOwner() {
        return owner;
    }

    public synchronized void offlineWake(String reason) {
        transition(
            State.OFFLINE_WAKE,
            MicrophoneOwner.OFFLINE_WAKE,
            reason
        );
    }

    public synchronized void opening(String reason) {
        transition(
            State.OPENING_SESSION,
            MicrophoneOwner.NONE,
            reason
        );
    }

    public synchronized void listeningStandard(String reason) {
        transition(
            State.LISTENING,
            MicrophoneOwner.STANDARD_RECOGNITION,
            reason
        );
    }

    public synchronized void listeningLive(String reason) {
        transition(
            State.LISTENING,
            MicrophoneOwner.LIVE_AUDIO,
            reason
        );
    }

    public synchronized void processing(
        boolean standardMicrophoneArmed,
        String reason
    ) {
        transition(
            State.PROCESSING,
            standardMicrophoneArmed
                ? MicrophoneOwner.STANDARD_RECOGNITION
                : MicrophoneOwner.LIVE_AUDIO,
            reason
        );
    }

    public synchronized void speaking(
        boolean standardMicrophoneArmed,
        String reason
    ) {
        transition(
            State.SPEAKING,
            standardMicrophoneArmed
                ? MicrophoneOwner.STANDARD_RECOGNITION
                : MicrophoneOwner.LIVE_AUDIO,
            reason
        );
    }

    public synchronized void interrupting(String reason) {
        transition(
            State.INTERRUPTING,
            owner,
            reason
        );
    }

    public synchronized void closing(String reason) {
        transition(
            State.CLOSING,
            MicrophoneOwner.NONE,
            reason
        );
    }

    public synchronized void recovering(String reason) {
        transition(
            State.RECOVERING,
            MicrophoneOwner.NONE,
            reason
        );
        diagnostics.recordRecovery(reason);
    }

    public synchronized void recordAudioProcessing(String summary) {
        diagnostics.recordAudioProcessing(summary);
    }

    public synchronized String summary() {
        return diagnostics.summary();
    }

    private void transition(
        State next,
        MicrophoneOwner nextOwner,
        String reason
    ) {
        if (!allowed(state).contains(next) && state != next) {
            diagnostics.recordInvalidTransition(
                state.name(),
                next.name(),
                reason
            );
        }

        state = next;
        owner = nextOwner;
        diagnostics.recordState(
            state.name(),
            owner.name(),
            reason
        );
    }

    private static EnumSet<State> allowed(State current) {
        return switch (current) {
            case OFFLINE_WAKE -> EnumSet.of(
                State.OFFLINE_WAKE,
                State.OPENING_SESSION,
                State.RECOVERING
            );
            case OPENING_SESSION -> EnumSet.of(
                State.LISTENING,
                State.PROCESSING,
                State.CLOSING,
                State.RECOVERING
            );
            case LISTENING -> EnumSet.of(
                State.LISTENING,
                State.PROCESSING,
                State.INTERRUPTING,
                State.CLOSING,
                State.RECOVERING
            );
            case PROCESSING -> EnumSet.of(
                State.PROCESSING,
                State.SPEAKING,
                State.INTERRUPTING,
                State.LISTENING,
                State.CLOSING,
                State.RECOVERING
            );
            case SPEAKING -> EnumSet.of(
                State.SPEAKING,
                State.INTERRUPTING,
                State.LISTENING,
                State.CLOSING,
                State.RECOVERING
            );
            case INTERRUPTING -> EnumSet.of(
                State.LISTENING,
                State.PROCESSING,
                State.CLOSING,
                State.RECOVERING
            );
            case CLOSING -> EnumSet.of(
                State.OFFLINE_WAKE,
                State.RECOVERING
            );
            case RECOVERING -> EnumSet.allOf(State.class);
        };
    }
}
