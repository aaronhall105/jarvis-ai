package com.aaron.jarvisvoice;

import org.json.JSONObject;
import org.junit.Test;
import static org.junit.Assert.*;

public class AutonomyPresentationTest {
    @Test public void phaseOneCannotOfferActivation() throws Exception {
        for (String phase : new String[]{"observe_only", "engineering", ""})
            assertFalse(AutonomyPresentation.resumeAvailable(new JSONObject().put("phase", phase)));
    }
    @Test public void failedControlNeverFailsOverToAnotherEndpoint() {
        assertFalse(AutonomyPresentation.retryHttpFailure("/control"));
        assertTrue(AutonomyPresentation.retryHttpFailure("/observe"));
    }
    @Test public void stoppedRemainsVisible() throws Exception {
        assertEquals("STOPPED", AutonomyPresentation.status(new JSONObject("{\"status\":\"STOPPED\",\"phase\":\"observe_only\"}")));
    }
    @Test public void activeRequiresNonObserverState() throws Exception {
        assertEquals("ACTIVE", AutonomyPresentation.status(new JSONObject("{\"status\":\"ACTIVE\",\"phase\":\"engineering\"}")));
        assertEquals("NEEDS ATTENTION", AutonomyPresentation.status(new JSONObject("{\"status\":\"ACTIVE\",\"phase\":\"observe_only\"}")));
    }
    @Test public void unknownIsNotSuccessful() {
        assertEquals("NEEDS ATTENTION", AutonomyPresentation.status(new JSONObject()));
    }
    @Test public void pausedAndAttentionPreserved() throws Exception {
        for (String value : new String[]{"PAUSED", "NEEDS ATTENTION"})
            assertEquals(value, AutonomyPresentation.status(new JSONObject().put("status", value)));
    }
    @Test public void noGoalMeansHonestIdle() {
        assertEquals("Nothing right now.", AutonomyPresentation.currentTitle(new JSONObject()));
    }
    @Test public void observerNeverClaimsBuildOrDeployment() throws Exception {
        for (String value : new String[]{"Building", "Updating Jarvis", "Finished"}) {
            JSONObject state = new JSONObject().put("phase", "observe_only")
                .put("current", new JSONObject().put("progress", value));
            assertEquals("Thinking", AutonomyPresentation.progress(state));
        }
    }
    @Test public void researchIsPresentedWithoutClaimingEngineering() throws Exception {
        JSONObject state = new JSONObject().put("phase", "observe_only")
            .put("current", new JSONObject().put("title", "Investigating evidence").put("progress", "Researching"));
        assertEquals("Researching", AutonomyPresentation.progress(state));
        assertEquals("Investigating evidence", AutonomyPresentation.currentTitle(state));
    }
    @Test public void unavailableCapabilityDoesNotBecomeAvailable() throws Exception {
        assertEquals("Not yet verified", AutonomyPresentation.capability(new JSONObject()));
        assertEquals("Not yet verified", AutonomyPresentation.capability(new JSONObject().put("state", "Available")));
        assertEquals("Setup required", AutonomyPresentation.capability(new JSONObject().put("verified", true).put("state", "Setup required")));
        assertEquals("Available", AutonomyPresentation.capability(new JSONObject().put("verified", true).put("state", "Available")));
    }
}
