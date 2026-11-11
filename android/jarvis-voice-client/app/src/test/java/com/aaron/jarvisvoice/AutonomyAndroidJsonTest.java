package com.aaron.jarvisvoice;

import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.TreeMap;
import static org.junit.Assert.*;

/** Exercise platform org.json, not only the different JVM org.json library. */
@RunWith(RobolectricTestRunner.class)
@Config(manifest = Config.NONE, sdk = 35)
public class AutonomyAndroidJsonTest extends AutonomyControlTest {
    @Test public void androidEscapingAndWholeDoubleCannotChangeSignedBytes() throws Exception {
        JSONArray paths = new JSONArray().put("bridge/app/ai_engine.py");
        assertTrue(paths.toString().contains("\\/"));
        TreeMap<String, Object> fields = new TreeMap<>();
        fields.put("allowed_paths", paths);
        fields.put("max_cost_usd", 1.0);
        String expected = "{\"allowed_paths\":[\"bridge/app/ai_engine.py\"],\"max_cost_usd\":1}";
        assertEquals(expected, AutonomyControl.canonical(fields));
        JSONObject wire = new JSONObject(new JSONObject(expected).toString());
        fields.put("allowed_paths", wire.getJSONArray("allowed_paths"));
        fields.put("max_cost_usd", wire.get("max_cost_usd"));
        assertEquals(expected, AutonomyControl.canonical(fields));
    }
}
