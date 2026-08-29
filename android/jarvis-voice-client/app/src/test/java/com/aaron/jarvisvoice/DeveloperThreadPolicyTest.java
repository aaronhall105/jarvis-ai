package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class DeveloperThreadPolicyTest {
    @Test public void jsonNullNeverBecomesVisibleChatTitle() {
        assertEquals("Development chat", DeveloperThreadPolicy.displayTitle("null", "null"));
        assertEquals("Fix Wear audio", DeveloperThreadPolicy.displayTitle("null", "Fix Wear audio"));
    }

    @Test public void explicitNameWins() {
        assertEquals("Wear v1", DeveloperThreadPolicy.displayTitle("Wear v1", "Old preview"));
    }
}
