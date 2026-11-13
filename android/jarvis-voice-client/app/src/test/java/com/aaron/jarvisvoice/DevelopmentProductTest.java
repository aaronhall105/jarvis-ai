package com.aaron.jarvisvoice;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;
import java.nio.charset.StandardCharsets;
import java.security.KeyFactory;
import java.security.Signature;
import java.security.spec.X509EncodedKeySpec;
import java.util.Base64;
import java.util.TreeMap;
import static org.junit.Assert.*;

public class DevelopmentProductTest {
    private static final String ID = "00000000-0000-4000-8000-000000000001";

    static JSONObject processProposal() throws Exception {
        try (java.io.InputStream input = DevelopmentProductTest.class.getResourceAsStream("/development_process_proposal.json")) {
            assertNotNull(input);
            return new JSONObject(new String(input.readAllBytes(), StandardCharsets.UTF_8));
        }
    }

    static JSONObject preparation() throws Exception {
        return new JSONObject().put("version", 1).put("action", "prepare_development")
            .put("request_id", ID).put("candidate_id", 78).put("source_sha", "a".repeat(40))
            .put("evidence_sha256", "b".repeat(64)).put("candidate_sha256", "c".repeat(64))
            .put("goal", "Reduce response latency without changing tool behavior")
            .put("allowed_paths", new JSONArray().put("bridge/app/ai_engine.py").put("bridge/tests/test_executive_agent.py"))
            .put("provider", "openai").put("model", "approved-model").put("max_calls", 1)
            .put("max_cost_usd", 1).put("deadline_seconds", 120).put("guardian_generation", ID)
            .put("expires_at", System.currentTimeMillis() / 1000 + 120);
    }

    static JSONObject activation() throws Exception {
        return new JSONObject().put("version", 1).put("action", "activate_development")
            .put("request_id", ID).put("task_id", ID).put("approval_id", "approval-78")
            .put("source_sha", "a".repeat(40)).put("parameters_sha256", "b".repeat(64))
            .put("guardian_generation", ID).put("expires_at", System.currentTimeMillis() / 1000 + 120);
    }

    static JSONObject integration() throws Exception {
        return new JSONObject().put("version", 1).put("action", "create_development_pr")
            .put("request_id", ID).put("task_id", ID).put("repository", "aaronhall105/jarvis-ai")
            .put("base_branch", "jarvis/unified-production").put("base_sha", "a".repeat(40))
            .put("base_tree_sha", "b".repeat(40)).put("head_tree_sha", "c".repeat(40))
            .put("allowed_paths", new JSONArray().put("bridge/app/ai_engine.py").put("bridge/tests/test_executive_agent.py"))
            .put("parameters_sha256", "d".repeat(64)).put("evidence_sha256", "e".repeat(64))
            .put("diff_sha256", "f".repeat(64)).put("proposal_sha256", "1".repeat(64))
            .put("expires_at", System.currentTimeMillis() / 1000 + 300);
    }

    @Test public void distinctDomainsPreserveExactTaskAndProposalBindings() throws Exception {
        assertNotEquals(AutonomyControl.PREPARATION_DOMAIN, AutonomyControl.ACTIVATION_DOMAIN);
        assertNotEquals(AutonomyControl.INTEGRATION_DOMAIN, AutonomyControl.ACTIVATION_DOMAIN);
        assertEquals(78, AutonomyControl.preparationFields(preparation()).get("candidate_id"));
        assertEquals("approval-78", AutonomyControl.activationFields(activation()).get("approval_id"));
        assertEquals("f".repeat(64), AutonomyControl.integrationFields(integration()).get("diff_sha256"));
        assertFalse(AutonomyControl.canonical(AutonomyControl.integrationFields(integration())).contains("signature"));
    }

    @Test public void actualPythonSignaturesVerifyWithAndroidCanonicalBytesAndRejectCrossDomainReplay() throws Exception {
        try (java.io.InputStream input = getClass().getResourceAsStream("/development_product_signatures.json")) {
            assertNotNull(input);
            JSONArray fixtures = new JSONArray(new String(input.readAllBytes(), StandardCharsets.UTF_8));
            for (int i = 0; i < fixtures.length(); i++) {
                JSONObject fixture = fixtures.getJSONObject(i);
                JSONObject wire = new JSONObject(fixture.getJSONObject("fields").toString());
                TreeMap<String, Object> fields = new TreeMap<>();
                wire.keys().forEachRemaining(name -> {
                    try { fields.put(name, wire.get(name)); }
                    catch (Exception invalid) { throw new IllegalArgumentException(invalid); }
                });
                String canonical = AutonomyControl.canonical(fields);
                assertEquals(fixture.getString("canonical"), canonical);
                java.security.PublicKey key = KeyFactory.getInstance("EC").generatePublic(new X509EncodedKeySpec(
                    Base64.getDecoder().decode(fixture.getString("public_key"))));
                byte[] signature = Base64.getDecoder().decode(fixture.getString("signature"));
                Signature verifier = Signature.getInstance("SHA256withECDSA");
                verifier.initVerify(key);
                verifier.update((fixture.getString("domain") + canonical).getBytes(StandardCharsets.UTF_8));
                assertTrue(verifier.verify(signature));
                verifier.initVerify(key);
                verifier.update(("jarvis-development-admission-v1\n" + canonical).getBytes(StandardCharsets.UTF_8));
                assertFalse("development approval cannot substitute for product authority", verifier.verify(signature));
                fields.put("request_id", "00000000-0000-4000-8000-000000000002");
                verifier.initVerify(key);
                verifier.update((fixture.getString("domain") + AutonomyControl.canonical(fields)).getBytes(StandardCharsets.UTF_8));
                assertFalse("changed approval identity accepted", verifier.verify(signature));
            }
        }
    }

    @Test public void eachAuthorityRejectsOtherDomainsAndCallerAuthorityFields() throws Exception {
        for (JSONObject wrong : new JSONObject[]{activation(), integration()}) {
            try { AutonomyControl.preparationFields(wrong); fail("cross-domain approval accepted"); }
            catch (IllegalArgumentException expected) { }
        }
        for (String field : new String[]{"signature", "key_id", "trusted", "merge"}) {
            try { AutonomyControl.integrationFields(integration().put(field, "forged")); fail("caller authority accepted"); }
            catch (IllegalArgumentException expected) { }
        }
    }

    @Test public void expiredMalformedProtectedOrExcessBudgetPreviewsCannotBeSigned() throws Exception {
        for (JSONObject invalid : new JSONObject[]{preparation().put("expires_at", 1),
                preparation().put("max_calls", 5), preparation().put("max_cost_usd", 2),
                preparation().put("max_cost_usd", "NaN"), preparation().put("deadline_seconds", 1.5),
                preparation().put("candidate_id", 1.5), preparation().put("source_sha", "current"),
                preparation().put("allowed_paths", new JSONArray().put("tools/autonomy_guardian.py"))}) {
            try { AutonomyControl.preparationFields(invalid); fail("invalid preparation accepted"); }
            catch (IllegalArgumentException expected) { }
        }
        try { AutonomyControl.activationFields(activation().put("guardian_generation", "stale")); fail(); }
        catch (IllegalArgumentException expected) { }
        try { AutonomyControl.integrationFields(integration().put("repository", "other/repository")); fail(); }
        catch (IllegalArgumentException expected) { }
        JSONObject incomplete = integration(); incomplete.remove("evidence_sha256");
        try { AutonomyControl.integrationFields(incomplete); fail(); }
        catch (IllegalArgumentException expected) { }
    }

    @Test public void requestEventPreservesServerIdentityForNativeReview() throws Exception {
        JSONObject response = new JSONObject().put("request_id", ID).put("candidate_id", 78).put("success", true);
        RealtimeProtocol.Event event = RealtimeProtocol.parse(new JSONObject().put("type", "development.request")
            .put("development_request", response).toString());
        assertEquals(ID, event.developmentRequest.getString("request_id"));
        assertEquals(78, event.developmentRequest.getInt("candidate_id"));
    }

    @Test public void onlyReadOperationsMayFailOverAndMutationsNeverRetry() {
        for (String path : new String[]{"/control", "/development/start", "/development/activate", "/development/publish"})
            assertFalse(path, AutonomyPresentation.retryHttpFailure(path));
        assertTrue(AutonomyPresentation.retryHttpFailure("/development/proposal/" + ID));
    }

    @Test public void successfulHttpResponseWithoutExactAcknowledgementIsNotSuccess() throws Exception {
        JSONObject request = preparation();
        JSONObject accepted = new JSONObject().put("ok", true).put("category", "accepted")
            .put("status", "preparation_requested").put("request_id", ID).put("task_id", JSONObject.NULL);
        assertTrue(AutonomyPresentation.productAcknowledged("/development/start", request, accepted));
        assertFalse(AutonomyPresentation.productAcknowledged("/development/start", request, new JSONObject().put("ok", true)));
        assertFalse(AutonomyPresentation.productAcknowledged("/development/activate", activation(), accepted));
        assertFalse(AutonomyPresentation.productAcknowledged("/development/start", request,
            accepted.put("request_id", "00000000-0000-4000-8000-000000000002")));
        JSONObject published = new JSONObject().put("ok", true).put("category", "accepted")
            .put("url", "https://github.com/aaronhall105/jarvis-ai/pull/123")
            .put("result", new JSONObject().put("status", "PR_REVIEW_REQUIRED").put("number", 123)
                .put("head_sha", "c".repeat(40)).put("base_sha", "a".repeat(40)).put("proposal_sha256", "1".repeat(64)));
        assertTrue(AutonomyPresentation.productAcknowledged("/development/publish", integration(), published));
        published.put("url", "https://example.invalid/untrusted");
        assertFalse(AutonomyPresentation.productAcknowledged("/development/publish", integration(), published));
    }

    @Test public void proposalRequiresDisplayedDiffAndSuccessfulBoundEvidence() throws Exception {
        JSONObject proposal = integration();
        proposal.remove("version"); proposal.remove("action"); proposal.remove("expires_at"); proposal.remove("request_id");
        String diff = "diff --git a/bridge/app/ai_engine.py b/bridge/app/ai_engine.py\n";
        JSONArray checks = new JSONArray();
        for (String name : new String[]{"pytest", "ruff", "bandit", "compile"})
            checks.put(new JSONObject().put("name", name).put("returncode", 0).put("duration_ms", 5)
                .put("command", new JSONArray().put(name)).put("output_sha256", "2".repeat(64))
                .put("test_counts", new JSONObject().put("executed", 2).put("failed", 0).put("collected", 2)));
        proposal.put("diff", diff).put("diff_sha256", DevelopmentProposal.sha256(diff))
            .put("changed_files", new JSONArray().put("bridge/app/ai_engine.py"))
            .put("checks", checks)
            .put("provider_calls", 1).put("cost_usd", 0.02).put("risk", "owner_review_required");
        JSONObject preview = new JSONObject().put("version", 3).put("status", "ready").put("proposal", proposal);
        assertSame(proposal, DevelopmentProposal.verified(preview, ID));
        JSONObject approval = integration().put("diff_sha256", DevelopmentProposal.sha256(diff));
        assertTrue(DevelopmentProposal.matchesApproval(proposal, approval));
        assertFalse(DevelopmentProposal.matchesApproval(proposal, approval.put("evidence_sha256", "0".repeat(64))));
        proposal.put("diff", diff + "+unreviewed edit\n");
        try { DevelopmentProposal.verified(preview, ID); fail("different displayed diff accepted"); }
        catch (IllegalArgumentException expected) { }
        proposal.put("diff", diff).put("checks", new JSONArray());
        try { DevelopmentProposal.verified(preview, ID); fail("missing check evidence accepted"); }
        catch (IllegalArgumentException expected) { }
        proposal.put("checks", new JSONArray().put(new JSONObject().put("name", "pytest").put("returncode", 1)
            .put("output_sha256", "2".repeat(64))));
        try { DevelopmentProposal.verified(preview, ID); fail("failed tests accepted"); }
        catch (IllegalArgumentException expected) { }
    }

    @Test public void reconnectOnlyOpensExactBoundPullRequestAndNeverUncertainHandoff() throws Exception {
        JSONObject proposal = integration();
        JSONObject receipt = new JSONObject().put("status", "PR_REVIEW_REQUIRED").put("number", 123)
            .put("base_sha", proposal.getString("base_sha")).put("head_sha", "2".repeat(40))
            .put("proposal_sha256", proposal.getString("proposal_sha256"))
            .put("url", "https://github.com/aaronhall105/jarvis-ai/pull/123");
        assertEquals(receipt.getString("url"), DevelopmentProposal.verifiedPullRequestUrl(proposal, receipt));
        for (String status : new String[]{"DISPATCHING", "RECONCILIATION_REQUIRED", "UNAVAILABLE"})
            assertNull(DevelopmentProposal.verifiedPullRequestUrl(proposal, new JSONObject(receipt.toString()).put("status", status)));
        for (String url : new String[]{"https://example.invalid/pull/123", "https://github.com/aaronhall105/jarvis-ai/pull/124",
                "https://github.com/aaronhall105/jarvis-ai/pull/123?redirect=other", "javascript:alert(1)"})
            assertNull(DevelopmentProposal.verifiedPullRequestUrl(proposal, new JSONObject(receipt.toString()).put("url", url)));
        assertNull(DevelopmentProposal.verifiedPullRequestUrl(proposal, receipt.put("proposal_sha256", "3".repeat(64))));
    }

    @Test public void actualProcessWorkerEvidenceIsCompatibleWithNativeProposalReview() throws Exception {
        // Captured from the disposable root publisher, actual bounded worker and
        // authenticated gateway. No receipts or counts were synthesized for Android.
        JSONObject preview = processProposal();
        JSONObject request = preview.getJSONObject("request");
        JSONObject proposal = DevelopmentProposal.verified(preview, request.getString("task_id"));
        assertTrue(DevelopmentProposal.matchesApproval(proposal, request));
        assertEquals(2, proposal.getInt("provider_calls"));
        assertEquals(0.0004, proposal.getDouble("cost_usd"), 0.000001);
        assertEquals(1, proposal.getJSONArray("checks").getJSONObject(0).getJSONObject("test_counts").getInt("executed"));
        // Keep the captured expiry unchanged. This durable evidence test never
        // signs or converts a historical preview into live authority.
        assertEquals(1794558487L, request.getLong("expires_at"));
        proposal.put("diff", proposal.getString("diff") + "+unverified\n");
        try { DevelopmentProposal.verified(preview, request.getString("task_id")); fail("tampered process diff accepted"); }
        catch (IllegalArgumentException expected) { }
    }
}
