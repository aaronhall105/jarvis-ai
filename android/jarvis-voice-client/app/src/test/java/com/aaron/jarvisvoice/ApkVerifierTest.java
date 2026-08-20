package com.aaron.jarvisvoice;
import java.util.Set;
import org.junit.Test;
import static org.junit.Assert.*;

public final class ApkVerifierTest {
    private static void verify(String pkg, String name, long code, Set<String> cert) {
        ApkVerifier.validateIdentity("com.aaron.jarvisvoice", pkg, "19.0.0-alpha16", name,
            190160, code, 190150, Set.of("trusted"), cert);
    }
    @Test public void validJarvisIdentityAccepted() { verify("com.aaron.jarvisvoice", "19.0.0-alpha16", 190160, Set.of("trusted")); }
    @Test public void wrongPackageRejected() { assertThrows(SecurityException.class, () -> verify("evil.app", "19.0.0-alpha16", 190160, Set.of("trusted"))); }
    @Test public void wrongVersionRejected() { assertThrows(SecurityException.class, () -> verify("com.aaron.jarvisvoice", "19.0.0-alpha17", 190160, Set.of("trusted"))); }
    @Test public void wrongVersionCodeRejected() { assertThrows(SecurityException.class, () -> verify("com.aaron.jarvisvoice", "19.0.0-alpha16", 190161, Set.of("trusted"))); }
    @Test public void signingMismatchRejected() { assertThrows(SecurityException.class, () -> verify("com.aaron.jarvisvoice", "19.0.0-alpha16", 190160, Set.of("other"))); }
    @Test public void equalVersionCodeRejected() { assertThrows(SecurityException.class, () -> ApkVerifier.validateIdentity("com.aaron.jarvisvoice", "com.aaron.jarvisvoice", "19.0.0-alpha15", "19.0.0-alpha15", 190150, 190150, 190150, Set.of("trusted"), Set.of("trusted"))); }
}
