package com.aaron.jarvisvoice;

import static org.junit.Assert.*;
import org.junit.Test;

public class DeveloperRoutingPolicyTest {
    @Test public void modesRouteToSeparateBackends() {
        assertFalse(DeveloperRoutingPolicy.routesToDeveloper(AssistantMode.JARVIS));
        assertTrue(DeveloperRoutingPolicy.routesToDeveloper(AssistantMode.DEVELOPER));
    }

    @Test public void composerAlwaysReflectsRoutingMode() {
        assertEquals("Message Jarvis", DeveloperRoutingPolicy.placeholder(AssistantMode.JARVIS));
        assertEquals("Message Developer", DeveloperRoutingPolicy.placeholder(AssistantMode.DEVELOPER));
    }
}
