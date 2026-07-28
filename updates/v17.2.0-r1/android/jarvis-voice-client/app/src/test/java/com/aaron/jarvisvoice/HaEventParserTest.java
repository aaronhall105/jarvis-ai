package com.aaron.jarvisvoice;

import org.json.JSONObject;
import org.junit.Test;
import static org.junit.Assert.*;

public class HaEventParserTest {
    @Test public void parsesIntentEnd() throws Exception {
        JSONObject root = new JSONObject("{\"type\":\"event\",\"event\":{\"type\":\"intent-end\",\"data\":{\"intent_output\":{\"conversation_id\":\"abc\",\"continue_conversation\":true,\"response\":{\"speech\":{\"plain\":{\"speech\":\"Done\"}}}}}}}");
        HaEventParser.ParsedEvent parsed = HaEventParser.parse(root);
        assertEquals("intent-end", parsed.type);
        assertEquals("Done", parsed.speech);
        assertEquals("abc", parsed.conversationId);
        assertTrue(parsed.continueConversation);
    }
    @Test public void parsesTtsUrl() throws Exception {
        JSONObject root = new JSONObject("{\"type\":\"event\",\"event\":{\"type\":\"tts-end\",\"data\":{\"tts_output\":{\"url\":\"/api/tts_proxy/test.mp3\"}}}}");
        assertEquals("/api/tts_proxy/test.mp3", HaEventParser.parse(root).ttsUrl);
    }
}
