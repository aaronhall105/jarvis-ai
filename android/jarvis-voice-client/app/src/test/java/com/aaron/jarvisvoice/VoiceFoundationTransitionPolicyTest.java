package com.aaron.jarvisvoice;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.EnumSet;

public final class VoiceFoundationTransitionPolicyTest {
    @Test public void expectedProductionPathIsValid() {
        assertTrue(allows(
            VoiceFoundationStateMachine.State.RECOVERING,
            VoiceFoundationStateMachine.State.OFFLINE_WAKE
        ));
        assertTrue(allows(
            VoiceFoundationStateMachine.State.OFFLINE_WAKE,
            VoiceFoundationStateMachine.State.OPENING_SESSION
        ));
        assertTrue(allows(
            VoiceFoundationStateMachine.State.OPENING_SESSION,
            VoiceFoundationStateMachine.State.LISTENING
        ));
        assertTrue(allows(
            VoiceFoundationStateMachine.State.LISTENING,
            VoiceFoundationStateMachine.State.PROCESSING
        ));
        assertTrue(allows(
            VoiceFoundationStateMachine.State.PROCESSING,
            VoiceFoundationStateMachine.State.SPEAKING
        ));
        assertTrue(allows(
            VoiceFoundationStateMachine.State.SPEAKING,
            VoiceFoundationStateMachine.State.LISTENING
        ));
        assertTrue(allows(
            VoiceFoundationStateMachine.State.LISTENING,
            VoiceFoundationStateMachine.State.CLOSING
        ));
        assertTrue(allows(
            VoiceFoundationStateMachine.State.CLOSING,
            VoiceFoundationStateMachine.State.OFFLINE_WAKE
        ));
    }

    @Test public void wakeCannotJumpDirectlyToSpeaking() {
        assertFalse(allows(
            VoiceFoundationStateMachine.State.OFFLINE_WAKE,
            VoiceFoundationStateMachine.State.SPEAKING
        ));
    }

    private static boolean allows(
        VoiceFoundationStateMachine.State from,
        VoiceFoundationStateMachine.State to
    ) {
        EnumSet<VoiceFoundationStateMachine.State> allowed =
            switch (from) {
                case OFFLINE_WAKE -> EnumSet.of(
                    VoiceFoundationStateMachine.State.OFFLINE_WAKE,
                    VoiceFoundationStateMachine.State.OPENING_SESSION,
                    VoiceFoundationStateMachine.State.RECOVERING
                );
                case OPENING_SESSION -> EnumSet.of(
                    VoiceFoundationStateMachine.State.LISTENING,
                    VoiceFoundationStateMachine.State.PROCESSING,
                    VoiceFoundationStateMachine.State.CLOSING,
                    VoiceFoundationStateMachine.State.RECOVERING
                );
                case LISTENING -> EnumSet.of(
                    VoiceFoundationStateMachine.State.LISTENING,
                    VoiceFoundationStateMachine.State.PROCESSING,
                    VoiceFoundationStateMachine.State.INTERRUPTING,
                    VoiceFoundationStateMachine.State.CLOSING,
                    VoiceFoundationStateMachine.State.RECOVERING
                );
                case PROCESSING -> EnumSet.of(
                    VoiceFoundationStateMachine.State.PROCESSING,
                    VoiceFoundationStateMachine.State.SPEAKING,
                    VoiceFoundationStateMachine.State.INTERRUPTING,
                    VoiceFoundationStateMachine.State.LISTENING,
                    VoiceFoundationStateMachine.State.CLOSING,
                    VoiceFoundationStateMachine.State.RECOVERING
                );
                case SPEAKING -> EnumSet.of(
                    VoiceFoundationStateMachine.State.SPEAKING,
                    VoiceFoundationStateMachine.State.INTERRUPTING,
                    VoiceFoundationStateMachine.State.LISTENING,
                    VoiceFoundationStateMachine.State.CLOSING,
                    VoiceFoundationStateMachine.State.RECOVERING
                );
                case INTERRUPTING -> EnumSet.of(
                    VoiceFoundationStateMachine.State.LISTENING,
                    VoiceFoundationStateMachine.State.PROCESSING,
                    VoiceFoundationStateMachine.State.CLOSING,
                    VoiceFoundationStateMachine.State.RECOVERING
                );
                case CLOSING -> EnumSet.of(
                    VoiceFoundationStateMachine.State.OFFLINE_WAKE,
                    VoiceFoundationStateMachine.State.RECOVERING
                );
                case RECOVERING ->
                    EnumSet.allOf(
                        VoiceFoundationStateMachine.State.class
                    );
            };

        return allowed.contains(to);
    }
}
