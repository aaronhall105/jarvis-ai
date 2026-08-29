package com.aaron.jarvisvoice;
import static org.junit.Assert.*;
import org.junit.Test;
public class DeveloperEndpointPolicyTest {
    @Test public void wifiPrefersLanAndMobilePrefersSecureRemote() {
        assertEquals("http://lan", DeveloperEndpointPolicy.order(true, "http://lan", "https://remote").get(0));
        assertEquals("https://remote", DeveloperEndpointPolicy.order(false, "http://lan", "https://remote").get(0));
    }
}
