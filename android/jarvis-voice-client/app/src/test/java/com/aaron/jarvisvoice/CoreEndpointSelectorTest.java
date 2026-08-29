package com.aaron.jarvisvoice;

import static org.junit.Assert.*;
import java.util.List;
import org.junit.Test;

public class CoreEndpointSelectorTest {
    private static final String LAN = "http://192.168.1.40:8000";
    private static final String REMOTE = "http://100.127.215.111:8000";

    @Test public void wifiPrefersReachableLanThenSecureRemoteFallback() {
        assertEquals(List.of(LAN, REMOTE), CoreEndpointSelector.preferenceOrder(true, LAN, REMOTE));
    }

    @Test public void mobilePrefersSecureRemoteThenLanRecoveryProbe() {
        assertEquals(List.of(REMOTE, LAN), CoreEndpointSelector.preferenceOrder(false, LAN, REMOTE));
    }

    @Test public void missingRemoteNeverInventsPublicEndpoint() {
        assertEquals(List.of(LAN), CoreEndpointSelector.preferenceOrder(false, LAN, ""));
    }
}
