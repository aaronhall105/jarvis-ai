package com.aaron.jarvisvoice;
import org.junit.Test;
import static org.junit.Assert.*;
public final class UpdateVersionAlpha15Test {
    @Test public void releaseIdentityIsAlpha17WithoutProtocolBump() {
        assertEquals("19.0.0-alpha22", JarvisVersion.RELEASE);
        assertEquals(2, JarvisVersion.REALTIME_PROTOCOL);
    }
}
