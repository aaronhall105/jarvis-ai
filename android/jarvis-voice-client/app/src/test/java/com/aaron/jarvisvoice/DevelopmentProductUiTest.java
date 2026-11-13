package com.aaron.jarvisvoice;

import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.HashSet;
import java.util.Set;
import org.json.JSONObject;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;
import org.robolectric.android.controller.ActivityController;
import static org.junit.Assert.*;

/** Native view coverage only; never calls a server or accesses a signing key. */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35)
public class DevelopmentProductUiTest {
    @Test public void unavailableAndExpiredEnvelopesNeverOfferDevelopmentApproval() throws Exception {
        try (ActivityController<AutonomyActivity> screen = Robolectric.buildActivity(AutonomyActivity.class).create()) {
            AutonomyActivity activity = screen.get();
            JSONObject state = new JSONObject().put("status", "STOPPED")
                .put("development_status", new JSONObject().put("status", "idle"))
                .put("development_task", new JSONObject().put("status", "unavailable"));
            render(activity, state);
            assertFalse(buttons(activity).contains("Authorize this development task"));
            JSONObject expired = new JSONObject().put("expires_at", 1).put("goal", "Expired improvement");
            render(activity, state.put("development_task", expired));
            assertFalse(buttons(activity).contains("Authorize this development task"));
        }
    }

    @Test public void activationAndProposalNeedProtectedProofAndRemainSeparate() throws Exception {
        try (ActivityController<AutonomyActivity> screen = Robolectric.buildActivity(AutonomyActivity.class).create()) {
            AutonomyActivity activity = screen.get();
            JSONObject lifecycle = new JSONObject().put("task_id", "00000000-0000-4000-8000-000000000001")
                .put("status", "approval_requested").put("lifecycle_state", "APPROVAL_REQUESTED");
            JSONObject state = new JSONObject().put("status", "STOPPED").put("development_status", lifecycle);
            JSONObject task = DevelopmentProductTest.preparation();
            task.remove("candidate_id"); task.remove("candidate_sha256"); task.remove("evidence_sha256"); task.remove("request_id");
            task.put("action", "approve_development").put("task_id", lifecycle.getString("task_id"))
                .put("request_ref", "approval-78").put("parameters_sha256", "a".repeat(64));
            state.put("development_task", task);
            render(activity, state);
            assertFalse(buttons(activity).contains("Check separate activation prerequisites"));
            assertTrue(buttons(activity).contains("Authorize this development task"));
            render(activity, state.put("development_status", lifecycle.put("approval_accepted", true)));
            assertTrue(buttons(activity).contains("Check separate activation prerequisites"));
            assertFalse(buttons(activity).contains("Authorize this development task"));
            assertFalse(buttons(activity).contains("Authorize separate task activation"));
            lifecycle.put("status", "review_required").put("proposal_available", false);
            render(activity, state);
            assertFalse(buttons(activity).contains("Review verified code proposal"));
            lifecycle.put("proposal_available", true);
            render(activity, state);
            assertTrue(buttons(activity).contains("Review verified code proposal"));
            assertFalse(buttons(activity).contains("Authorize pull request for this proposal"));
        }
    }

    @Test public void recordedOrUncertainPublicationCannotOfferDuplicateApproval() throws Exception {
        try (ActivityController<AutonomyActivity> screen = Robolectric.buildActivity(AutonomyActivity.class).create()) {
            AutonomyActivity activity = screen.get();
            JSONObject preview = DevelopmentProductTest.processProposal();
            JSONObject proposal = preview.getJSONObject("proposal");
            set(activity, "productKind", "proposal");
            set(activity, "productIdentity", proposal.getString("task_id"));
            JSONObject state = new JSONObject().put("status", "STOPPED");
            for (String status : new String[]{"DISPATCHING", "RECONCILIATION_REQUIRED", "UNAVAILABLE", "PR_REVIEW_REQUIRED"}) {
                JSONObject receipt = new JSONObject().put("status", status).put("number", 123)
                    .put("base_sha", proposal.getString("base_sha")).put("head_sha", "2".repeat(40))
                    .put("proposal_sha256", proposal.getString("proposal_sha256"))
                    .put("url", "https://github.com/aaronhall105/jarvis-ai/pull/123");
                set(activity, "productPreview", preview.put("request", JSONObject.NULL).put("handoff", receipt));
                render(activity, state);
                assertFalse(buttons(activity).contains("Authorize pull request for this proposal"));
                assertEquals(status.equals("PR_REVIEW_REQUIRED"), buttons(activity).contains("Open pull request"));
            }
        }
    }

    private static void set(AutonomyActivity activity, String name, Object value) throws Exception {
        Field field = AutonomyActivity.class.getDeclaredField(name); field.setAccessible(true); field.set(activity, value);
    }

    private static void render(AutonomyActivity activity, JSONObject state) throws Exception {
        Field field = AutonomyActivity.class.getDeclaredField("state"); field.setAccessible(true); field.set(activity, state);
        Method render = AutonomyActivity.class.getDeclaredMethod("render", String.class); render.setAccessible(true);
        render.invoke(activity, (Object) null);
    }

    private static Set<String> buttons(AutonomyActivity activity) {
        Set<String> labels = new HashSet<>();
        collect(activity.findViewById(android.R.id.content), labels);
        return labels;
    }

    private static void collect(View view, Set<String> labels) {
        if (view instanceof Button) labels.add(((Button) view).getText().toString());
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++) collect(group.getChildAt(i), labels);
        }
    }
}
