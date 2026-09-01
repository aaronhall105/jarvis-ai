package com.aaron.jarvisvoice;
import org.junit.Test;
import static org.junit.Assert.*;
public final class UpdateVersionAlpha15Test {
    @Test public void releaseIdentityIsAlpha27WithoutProtocolBump() {
        assertEquals("19.0.0-alpha27", JarvisVersion.RELEASE);
        assertEquals(2, JarvisVersion.REALTIME_PROTOCOL);
    }
}
