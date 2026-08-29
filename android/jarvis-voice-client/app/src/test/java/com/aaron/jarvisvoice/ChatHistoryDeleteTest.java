package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertTrue;

import android.app.Application;

import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35)
public final class ChatHistoryDeleteTest {
    @Test public void deletingCurrentConversationPreservesEveryOtherConversation() {
        Application context = RuntimeEnvironment.getApplication();
        context.getSharedPreferences("jarvis_chat_history", 0).edit().clear().commit();
        context.getSharedPreferences("jarvis_secure_store", 0).edit().clear().commit();

        ChatHistoryStore history = new ChatHistoryStore(context);
        String first = history.activeConversationId();
        history.add("user", "Keep this conversation");
        String deleted = history.createConversation();
        history.add("user", "Delete only this conversation");

        assertEquals(deleted, history.activeConversationId());
        assertTrue(history.deleteConversation(deleted));
        assertNotEquals(deleted, history.activeConversationId());
        assertTrue(history.conversations("").stream().anyMatch(item -> first.equals(item.id)));
        assertFalse(history.conversations("").stream().anyMatch(item -> deleted.equals(item.id)));
        assertEquals("Keep this conversation", history.list().get(0).text);
    }
}
