package com.aaron.jarvisvoice;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class ExpectedTransitionPolicyTest {
    @Test public void bargeInAndReconnectAreExpected() {
        assertTrue(ExpectedTransitionPolicy.isExpected(
            "SPEAKING",
            "LISTENING",
            "barge-in accepted"
        ));
        assertTrue(ExpectedTransitionPolicy.isExpected(
            "OFFLINE",
            "CONNECTING",
            "network restored"
        ));
    }

    @Test public void unrelatedOrderingFaultRemainsWarning() {
        assertFalse(ExpectedTransitionPolicy.isExpected(
            "IDLE",
            "SPEAKING",
            "unexpected callback ordering"
        ));
    }
}
