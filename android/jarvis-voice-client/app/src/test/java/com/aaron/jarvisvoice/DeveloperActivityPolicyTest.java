package com.aaron.jarvisvoice;

import org.json.JSONArray;
import org.json.JSONObject;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;
import org.junit.Test;

public class DeveloperActivityPolicyTest {
    @Test public void toolEventsUseFriendlyCardTitles() {
        assertEquals("Command", DeveloperActivityPolicy.title("commandExecution"));
        assertEquals("Files changed", DeveloperActivityPolicy.title("fileChange"));
        assertEquals("Developer activity", DeveloperActivityPolicy.title("unknown"));
    }

    @Test public void activityStatusIsHumanReadable() {
        assertEquals("Running…", DeveloperActivityPolicy.status("inProgress", false));
        assertEquals("Completed", DeveloperActivityPolicy.status("completed", true));
        assertEquals("Failed", DeveloperActivityPolicy.status("failed", true));
    }

    @Test public void exposesCommandOutputAndFileDiffDetails() throws Exception {
        JSONObject command = new JSONObject().put("type", "commandExecution")
            .put("command", "git status").put("aggregatedOutput", "clean").put("exitCode", 0);
        assertTrue(DeveloperActivityPolicy.details(command).contains("$ git status"));
        assertTrue(DeveloperActivityPolicy.details(command).contains("clean"));
        JSONObject file = new JSONObject().put("type", "fileChange").put("changes",
            new JSONArray().put(new JSONObject().put("path", "VoiceService.java").put("diff", "+ready")));
        assertTrue(DeveloperActivityPolicy.details(file).contains("VoiceService.java"));
        assertTrue(DeveloperActivityPolicy.details(file).contains("+ready"));
    }
}
