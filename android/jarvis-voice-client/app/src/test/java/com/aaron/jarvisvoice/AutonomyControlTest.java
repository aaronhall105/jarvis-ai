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
            .put("bridge/app/ai_engine.py").put("../outside.py"));
        AutonomyControl.signedDevelopment(task);
    }

    @Test public void developmentTaskAcceptsCandidate75Scope() throws Exception {
        TreeMap<String, Object> fields = AutonomyControl.developmentFields(developmentTask());
        org.json.JSONArray paths = (org.json.JSONArray) fields.get("allowed_paths");
        assertEquals("bridge/app/ai_engine.py", paths.getString(0));
        assertEquals("bridge/tests/test_executive_agent.py", paths.getString(1));
    }

    @Test public void signedScopeAcceptsOnlyExplicitBoundedRepairCallLimits() throws Exception {
        for (int calls : new int[]{1, 2, 3, 4}) {
            TreeMap<String, Object> fields = AutonomyControl.developmentFields(
                developmentTask().put("max_calls", calls));
            assertEquals(calls, fields.get("max_calls"));
            assertTrue(AutonomyControl.canonical(fields).contains("\"max_calls\":" + calls));
        }
        for (Object calls : new Object[]{0, -1, 5, 1.5, "2", true}) {
            try {
                AutonomyControl.developmentFields(developmentTask().put("max_calls", calls));
                fail("accepted invalid call limit " + calls);
            } catch (IllegalArgumentException expected) { }
        }
    }

    @Test public void configuredModelIsPreservedInExactSignedEnvelope() throws Exception {
        String model = "configured-coding-model:2026-01-01";
        TreeMap<String, Object> fields = AutonomyControl.developmentFields(
            developmentTask().put("model", model));
        assertEquals(model, fields.get("model"));
        assertEquals("openai", fields.get("provider"));
        assertTrue(AutonomyControl.canonical(fields).contains("\"model\":\"" + model + "\""));
        // The phone validates and signs the protected controller's exact model;
        // it does not select a model or assert availability/verified pricing.
        for (String invalid : new String[]{"", "../model", "model name", "model\n", "-model", "m".repeat(129)}) {
            try {
                AutonomyControl.developmentFields(developmentTask().put("model", invalid));
                fail("accepted malformed model");
            } catch (IllegalArgumentException expected) { }
        }
    }

    @Test public void approvalButtonRequiresCompleteCurrentSignerEnvelope() throws Exception {
        assertTrue(AutonomyPresentation.developmentApprovalAvailable(developmentTask()));
        assertFalse(AutonomyPresentation.developmentApprovalAvailable(null));
        assertFalse(AutonomyPresentation.developmentApprovalAvailable(new JSONObject()));
        assertFalse(AutonomyPresentation.developmentApprovalAvailable(
            new JSONObject().put("status", "unavailable")));
        assertFalse(AutonomyPresentation.developmentApprovalAvailable(
            developmentTask().put("expires_at", 1)));
        assertFalse(AutonomyPresentation.developmentApprovalAvailable(
            developmentTask().put("allowed_paths", new org.json.JSONArray().put("tools/autonomy_guardian.py"))));
        JSONObject missing = developmentTask(); missing.remove("parameters_sha256");
        assertFalse(AutonomyPresentation.developmentApprovalAvailable(missing));
        assertFalse(AutonomyPresentation.developmentApprovalAvailable(
            developmentTask().put("status", "approved")));
    }

    @Test(expected=IllegalArgumentException.class) public void developmentTaskRejectsProtectedPolicyPath() throws Exception {
        JSONObject task = developmentTask().put("allowed_paths", new org.json.JSONArray()
            .put("tools/supervised_development_worker.py"));
        AutonomyControl.signedDevelopment(task);
    }

    @Test(expected=IllegalArgumentException.class) public void developmentTaskRejectsNonCanonicalPath() throws Exception {
        JSONObject task = developmentTask().put("allowed_paths", new org.json.JSONArray()
            .put("bridge//app/ai_engine.py"));
        AutonomyControl.signedDevelopment(task);
    }

    @Test(expected=IllegalArgumentException.class) public void developmentTaskRejectsProviderAndCallLimitTampering() throws Exception {
        JSONObject task = developmentTask().put("provider", "other").put("max_calls", 2);
        AutonomyControl.signedDevelopment(task);
    }

    @Test(expected=IllegalArgumentException.class) public void developmentTaskRejectsMissingBinding() throws Exception {
        JSONObject task = developmentTask();
        task.remove("parameters_sha256");
        AutonomyControl.signedDevelopment(task);
    }

    @Test(expected=IllegalArgumentException.class) public void developmentTaskRejectsExpiredApproval() throws Exception {
        JSONObject task = developmentTask().put("expires_at", 1L);
        AutonomyControl.signedDevelopment(task);
    }

    @Test public void developmentSignatureNumbersSurviveJsonTransport() throws Exception {
        for (double cost : new double[] {0.0, -0.0, 1.0, 0.09, 0.00001, 0.000001}) {
            TreeMap<String, Object> fields = AutonomyControl.developmentFields(
                developmentTask().put("max_cost_usd", cost));
            String signedBytes = AutonomyControl.canonical(fields);
            JSONObject transmitted = new JSONObject(new JSONObject(signedBytes).toString());
            TreeMap<String, Object> received = new TreeMap<>();
            java.util.Iterator<String> keys = transmitted.keys();
            while (keys.hasNext()) { String key = keys.next(); received.put(key, transmitted.get(key)); }
            assertEquals(signedBytes, AutonomyControl.canonical(received));
            assertFalse(signedBytes.contains("\"max_cost_usd\":1.0"));
        }
    }

    @Test public void developmentCanonicalBytesMatchPythonVectors() throws Exception {
        try (java.io.InputStream input = getClass().getResourceAsStream("/development_canonical_vectors.json")) {
            assertNotNull(input);
            org.json.JSONArray vectors = new org.json.JSONArray(new String(input.readAllBytes(), StandardCharsets.UTF_8));
            for (int i = 0; i < vectors.length(); i++) {
                JSONObject vector = vectors.getJSONObject(i);
                JSONObject values = vector.getJSONObject("fields");
                TreeMap<String, Object> fields = new TreeMap<>();
                java.util.Iterator<String> keys = values.keys();
                while (keys.hasNext()) { String key = keys.next(); fields.put(key, values.get(key)); }
                assertEquals(vector.getString("canonical"), AutonomyControl.canonical(fields));
            }
        }
    }

    @Test public void pathsDoNotUsePlatformJsonArraySerialization() throws Exception {
        // Android framework JSONStringer escapes '/' while the JVM dependency
        // does not. A signature serializer must not use either implementation.
        org.json.JSONArray paths = new org.json.JSONArray() {
            @Override public String toString() { throw new AssertionError("platform JSON used for signature"); }
        };
        paths.put("bridge/app/ai_engine.py");
        TreeMap<String, Object> fields = new TreeMap<>();
        fields.put("allowed_paths", paths);
        assertEquals("{\"allowed_paths\":[\"bridge/app/ai_engine.py\"]}", AutonomyControl.canonical(fields));
    }

    @Test public void canonicalScopeMatchesSharedProtectedBoundaryVectors() throws Exception {
        try (java.io.InputStream input = getClass().getResourceAsStream("/development_scope_vectors.json")) {
            assertNotNull(input);
            JSONObject vectors = new JSONObject(new String(input.readAllBytes(), StandardCharsets.UTF_8));
            for (String verdict : new String[] {"allowed", "denied"}) {
                org.json.JSONArray paths = vectors.getJSONArray(verdict);
                for (int i = 0; i < paths.length(); i++) {
                    String path = paths.getString(i);
                    assertEquals(path, verdict.equals("allowed"), AutonomyControl.isCanonicalEditablePath(path));
                }
            }
        }
    }

    @Test public void signedScopeHasNoArbitraryGlobalFileCountLimit() throws Exception {
        JSONObject task = developmentTask();
        org.json.JSONArray paths = new org.json.JSONArray();
        for (int i = 0; i < 80; i++) paths.put(String.format(java.util.Locale.ROOT, "bridge/app/module_%03d.py", i));
        task.put("allowed_paths", paths);
        assertEquals(80, ((org.json.JSONArray) AutonomyControl.developmentFields(task).get("allowed_paths")).length());
    }

    private JSONObject developmentTask() throws Exception {
        return new JSONObject().put("version", 1).put("action", "approve_development")
            .put("goal", "Reduce response latency safely")
            .put("task_id", GENERATION).put("source_sha", "a".repeat(40))
            .put("parameters_sha256", "b".repeat(64))
            .put("request_ref", REQUEST).put("expires_at", System.currentTimeMillis() / 1000 + 45)
            .put("allowed_paths", new org.json.JSONArray().put("bridge/app/ai_engine.py")
                .put("bridge/tests/test_executive_agent.py"))
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
