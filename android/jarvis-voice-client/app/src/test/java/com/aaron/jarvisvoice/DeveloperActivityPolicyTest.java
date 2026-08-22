package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
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
}
