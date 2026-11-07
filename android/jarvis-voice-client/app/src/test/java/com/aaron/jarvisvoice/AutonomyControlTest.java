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

    @Test(expected=IllegalArgumentException.class) public void developmentTaskRejectsUnexpectedFields() throws Exception {
        JSONObject task = developmentTask(); task.put("signature", "forged");
        AutonomyControl.signedDevelopment(task);
    }

    @Test(expected=IllegalArgumentException.class) public void developmentTaskRejectsOutOfScopePath() throws Exception {
        JSONObject task = developmentTask(); task.put("allowed_paths", new org.json.JSONArray()
            .put("tools/developer_session_diagnostics.py").put("../outside.py"));
        AutonomyControl.signedDevelopment(task);
    }

    @Test(expected=IllegalArgumentException.class) public void developmentTaskRejectsExpiredApproval() throws Exception {
        JSONObject task = developmentTask().put("expires_at", 1L);
        AutonomyControl.signedDevelopment(task);
    }

    private JSONObject developmentTask() throws Exception {
        return new JSONObject().put("version", 1).put("action", "approve_development")
            .put("task_id", GENERATION).put("source_sha", "a".repeat(40))
            .put("parameters_sha256", "b".repeat(64))
            .put("request_ref", REQUEST).put("expires_at", System.currentTimeMillis() / 1000 + 45)
            .put("allowed_paths", new org.json.JSONArray().put("tools/developer_session_diagnostics.py")
                .put("tools/tests/test_developer_session_diagnostics.py"))
            .put("provider", "openai").put("model", "gpt-5.3-codex").put("max_calls", 1)
            .put("max_cost_usd", 1.0).put("deadline_seconds", 120)
            .put("guardian_generation", REQUEST);
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
