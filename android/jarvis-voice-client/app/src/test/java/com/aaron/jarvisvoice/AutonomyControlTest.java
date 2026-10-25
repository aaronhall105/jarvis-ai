package com.aaron.jarvisvoice;

import org.junit.Test;
import org.json.JSONObject;
import java.nio.charset.StandardCharsets;
import java.security.KeyFactory;
import java.security.Signature;
import java.security.spec.X509EncodedKeySpec;
import java.util.Base64;
import java.util.TreeMap;
import static org.junit.Assert.*;

public class AutonomyControlTest {
    private static final String GENERATION = "00000000-0000-4000-8000-000000000001";
    private static final String REQUEST = "00000000-0000-4000-8000-000000000002";
    @Test public void canonicalEnvelopeMatchesProtectedBrokerContract() {
        String keyId = "a".repeat(64);
        String expected = "{\"action\":\"stop\",\"expires_at\":12345,\"generation\":\"" + GENERATION
            + "\",\"key_id\":\"" + keyId + "\",\"request_id\":\"" + REQUEST + "\",\"version\":1}";
        assertEquals(expected, AutonomyControl.unsigned("stop", GENERATION, keyId, REQUEST, 12345));
    }
    @Test(expected=IllegalArgumentException.class) public void arbitraryActionRejected() {
        AutonomyControl.unsigned("shell", GENERATION, "a".repeat(64), REQUEST, 12345);
    }
    @Test(expected=IllegalArgumentException.class) public void invalidGenerationRejected() {
        AutonomyControl.unsigned("resume", "../invalid", "a".repeat(64), REQUEST, 12345);
    }
    @Test(expected=IllegalArgumentException.class) public void invalidKeyIdRejected() {
        AutonomyControl.unsigned("resume", GENERATION, "not-an-enrolled-key", REQUEST, 12345);
    }

    private JSONObject vectors() throws Exception {
        try (java.io.InputStream input = getClass().getResourceAsStream("/autonomy_operator_vectors.json")) {
            assertNotNull(input);
            return new JSONObject(new String(input.readAllBytes(), StandardCharsets.UTF_8));
        }
    }

    private void verifyVector(String name, String domain) throws Exception {
        JSONObject vectors = vectors();
        JSONObject vector = vectors.getJSONObject(name);
        String unsigned = vector.getString("unsigned");
        JSONObject fields = new JSONObject(unsigned);
        TreeMap<String, Object> sorted = new TreeMap<>();
        java.util.Iterator<String> names = fields.keys();
        while (names.hasNext()) { String key = names.next(); sorted.put(key, fields.get(key)); }
        assertEquals(unsigned, AutonomyControl.canonical(sorted));
        if (name.equals("control")) assertEquals(unsigned, AutonomyControl.unsigned(
            fields.getString("action"), fields.getString("generation"), fields.getString("key_id"),
            fields.getString("request_id"), fields.getLong("expires_at")));
        Signature verifier = Signature.getInstance("SHA256withECDSA");
        java.security.PublicKey publicKey = KeyFactory.getInstance("EC").generatePublic(
            new X509EncodedKeySpec(Base64.getDecoder().decode(vectors.getString("public_key"))));
        verifier.initVerify(publicKey);
        verifier.update((domain + unsigned).getBytes(StandardCharsets.UTF_8));
        byte[] signature = Base64.getDecoder().decode(vector.getString("signature"));
        assertTrue(verifier.verify(signature));
        verifier.initVerify(publicKey);
        verifier.update(("wrong-domain\n" + unsigned).getBytes(StandardCharsets.UTF_8));
        assertFalse(verifier.verify(signature));
    }

    @Test public void pythonControlSignatureAndCanonicalBytesVerifyInJava() throws Exception {
        verifyVector("control", "jarvis-autonomy-control-v1\n");
    }

    @Test public void pythonEnrollmentSignatureAndCanonicalBytesVerifyInJava() throws Exception {
        verifyVector("enrollment", "jarvis-autonomy-enrollment-v1\n");
    }
}
