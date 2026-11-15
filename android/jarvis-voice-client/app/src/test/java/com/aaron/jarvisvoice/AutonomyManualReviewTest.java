package com.aaron.jarvisvoice;

import static org.junit.Assert.*;
import java.util.UUID;
import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35)
public class AutonomyManualReviewTest {
    private JSONObject manualTask() throws Exception {
        return new JSONObject()
            .put("version", 2).put("action", "approve_development")
            .put("goal", "Improve a bounded diagnostic.")
            .put("task_id", UUID.randomUUID().toString())
            .put("request_ref", UUID.randomUUID().toString())
            .put("source_sha", "a".repeat(40)).put("parameters_sha256", "b".repeat(64))
            .put("allowed_paths", new JSONArray().put("tools/example.py"))
            .put("provider", "openai").put("model", "gpt-5.3-codex")
            .put("max_calls", 1).put("max_cost_usd", 1.0).put("deadline_seconds", 120)
            .put("expires_at", 0).put("guardian_generation", UUID.randomUUID().toString());
    }

    private void rejected(JSONObject task) {
        assertFalse(AutonomyPresentation.developmentApprovalAvailable(task));
    }

    @Test public void manualReviewHasNoCountdownButKeepsExecutionBounds() throws Exception {
        JSONObject task = manualTask();
        assertTrue(AutonomyPresentation.developmentApprovalAvailable(task));
        assertTrue(AutonomyControl.manualReview(task));
        assertEquals(2, AutonomyControl.developmentFields(task).get("version"));
        assertTrue(AutonomyControl.reviewDeadlineLabel(task).contains("Review deadline: none"));
        assertEquals(120, task.getInt("deadline_seconds"));
        assertEquals(1, task.getInt("max_calls"));
        assertNotEquals(AutonomyControl.developmentDomain(1), AutonomyControl.developmentDomain(2));
    }

    @Test public void legacyApprovalStillExpires() throws Exception {
        JSONObject task = manualTask().put("version", 1);
        rejected(task);
        task.put("expires_at", System.currentTimeMillis() / 1000 + 60);
        assertTrue(AutonomyPresentation.developmentApprovalAvailable(task));
        assertFalse(AutonomyControl.manualReview(task));
    }

    @Test public void unknownMissingAndCoercedManualFieldsAreRejected() throws Exception {
        rejected(manualTask().put("version", 3));
        rejected(manualTask().put("version", "2"));
        rejected(manualTask().put("expires_at", 0.0));
        rejected(manualTask().put("expires_at", "0"));
        rejected(manualTask().put("expires_at", false));
        rejected(manualTask().put("expires_at", -1));
        rejected(manualTask().put("expires_at", System.currentTimeMillis() / 1000 + 60));
        JSONObject missing = manualTask();
        missing.remove("expires_at");
        rejected(missing);
    }

    @Test public void manualModeCannotExpandPolicyOrDropBindings() throws Exception {
        rejected(manualTask().put("max_calls", 5));
        rejected(manualTask().put("max_cost_usd", 1.01));
        rejected(manualTask().put("deadline_seconds", 121));
        rejected(manualTask().put("allowed_paths", new JSONArray().put("tools/autonomy_broker.py")));
        JSONObject missing = manualTask();
        missing.remove("parameters_sha256");
        rejected(missing);
    }
}
