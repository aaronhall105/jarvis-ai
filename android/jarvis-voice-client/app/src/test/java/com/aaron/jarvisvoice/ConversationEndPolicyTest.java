package com.aaron.jarvisvoice;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class ConversationEndPolicyTest {
    @Test public void recognisesPoliteClosingPhrases() {
        assertTrue(ConversationEndPolicy.shouldEnd("Okay goodbye"));
        assertTrue(ConversationEndPolicy.shouldEnd("Thanks Jarvis"));
        assertTrue(ConversationEndPolicy.shouldEnd("That's all, thank you."));
        assertTrue(ConversationEndPolicy.shouldEnd("Please stop listening"));
    }

    @Test public void doesNotCloseForNormalSentences() {
        assertFalse(ConversationEndPolicy.shouldEnd("How do I say goodbye in French?"));
        assertFalse(ConversationEndPolicy.shouldEnd("Do not stop the music"));
        assertFalse(ConversationEndPolicy.shouldEnd("Tell Amber thanks from me"));
    }
}
