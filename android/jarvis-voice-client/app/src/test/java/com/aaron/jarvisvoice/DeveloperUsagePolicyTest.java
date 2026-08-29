package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class DeveloperUsagePolicyTest {
    @Test public void percentageIsAlwaysSafeForProgressBar() {
        assertEquals(0, DeveloperUsagePolicy.clampPercent(-4));
        assertEquals(37, DeveloperUsagePolicy.clampPercent(37));
        assertEquals(100, DeveloperUsagePolicy.clampPercent(180));
    }

    @Test public void commonCodexWindowsHaveCompactLabels() {
        assertEquals("5h", DeveloperUsagePolicy.windowLabel(300));
        assertEquals("weekly", DeveloperUsagePolicy.windowLabel(10080));
        assertEquals("90m", DeveloperUsagePolicy.windowLabel(90));
    }
}
