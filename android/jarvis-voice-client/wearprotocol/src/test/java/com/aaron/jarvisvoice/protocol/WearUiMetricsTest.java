package com.aaron.jarvisvoice.protocol;

import static org.junit.Assert.*;
import org.junit.Test;

public class WearUiMetricsTest {
    @Test public void smL315fUsesRoundSafeInsetsAndCompactVisibleControls() {
        assertEquals(18, WearUiMetrics.safeSideDp(226, true));
        assertTrue(WearUiMetrics.textActionVisibleDp() < WearUiMetrics.actionTouchTargetDp());
        assertTrue(WearUiMetrics.primaryActionVisibleDp() < WearUiMetrics.actionTouchTargetDp());
    }

    @Test public void dimensionsScaleWithoutExcessiveRoundPadding() {
        assertEquals(12, WearUiMetrics.safeSideDp(226, false));
        assertEquals(20, WearUiMetrics.safeSideDp(240, true));
        assertEquals(24, WearUiMetrics.safeSideDp(360, true));
    }
}
