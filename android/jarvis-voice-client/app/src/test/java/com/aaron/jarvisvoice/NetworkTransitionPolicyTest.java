package com.aaron.jarvisvoice;

import static org.junit.Assert.*;
import org.junit.Test;

public class NetworkTransitionPolicyTest {
    @Test public void wifiToMobileTriggersEndpointReevaluation() {
        assertTrue(NetworkTransitionPolicy.shouldReevaluate(true, true,
            NetworkTransitionPolicy.WIFI, NetworkTransitionPolicy.CELLULAR));
    }
    @Test public void mobileToWifiTriggersPreferredLanRecovery() {
        assertTrue(NetworkTransitionPolicy.shouldReevaluate(true, true,
            NetworkTransitionPolicy.CELLULAR, NetworkTransitionPolicy.WIFI));
    }
    @Test public void unchangedInternetDoesNotChurnConnection() {
        assertFalse(NetworkTransitionPolicy.shouldReevaluate(true, true,
            NetworkTransitionPolicy.WIFI, NetworkTransitionPolicy.WIFI));
    }
    @Test public void falseInternetStateStillTriggersReconnectLogic() {
        assertTrue(NetworkTransitionPolicy.shouldReevaluate(true, false,
            NetworkTransitionPolicy.WIFI, NetworkTransitionPolicy.NONE));
    }
}
