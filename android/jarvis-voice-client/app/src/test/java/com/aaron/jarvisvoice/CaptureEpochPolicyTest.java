package com.aaron.jarvisvoice;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class CaptureEpochPolicyTest {

    @Test
    public void currentRunningCaptureMayPublish() {
        assertTrue(
            CaptureEpochPolicy.mayPublish(
                true,
                12,
                12
            )
        );
    }

    @Test
    public void stoppedCaptureCannotPublish() {
        assertFalse(
            CaptureEpochPolicy.mayPublish(
                false,
                12,
                12
            )
        );
    }

    @Test
    public void oldCaptureCannotAffectReplacement() {
        assertFalse(
            CaptureEpochPolicy.mayPublish(
                true,
                12,
                13
            )
        );
    }
}
