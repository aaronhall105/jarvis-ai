package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.List;

import org.junit.Test;

public final class ProactiveUiPolicyTest {
    @Test public void labelsAreStable() {
        assertEquals("Critical", ProactiveUiPolicy.importanceLabel(98));
        assertEquals("High", ProactiveUiPolicy.importanceLabel(88));
    }

    @Test public void lockActionIsHidden() {
        ProactiveEvent event = new ProactiveEvent(
            "1", "security", "lock.front_door", "Lock", "Lock event",
            "test", 99, List.of("turn_off"), "active", 0L, null
        );
        assertTrue(ProactiveUiPolicy.sensitive(event.entityId));
        assertFalse(ProactiveUiPolicy.mayTurnOff(event));
    }

    @Test public void switchActionCanShow() {
        ProactiveEvent event = new ProactiveEvent(
            "1", "appliances", "switch.oven", "Oven", "Oven event",
            "test", 94, List.of("turn_off"), "active", 0L, null
        );
        assertTrue(ProactiveUiPolicy.mayTurnOff(event));
    }
}
