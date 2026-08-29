package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;
import static org.robolectric.Shadows.shadowOf;

import android.app.AlertDialog;
import android.content.Intent;
import android.view.Menu;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ImageButton;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.PopupMenu;
import android.widget.TextView;

import androidx.work.Configuration;
import androidx.work.testing.WorkManagerTestInitHelper;

import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;
import org.robolectric.shadow.api.Shadow;
import org.robolectric.shadows.ShadowAlertDialog;
import org.robolectric.shadows.ShadowPopupMenu;

import java.util.List;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35)
public final class Alpha17ExperienceTest {
    @Test public void normalChatUsesTheActualAlpha17NavigationAndComposer() {
        WorkManagerTestInitHelper.initializeTestWorkManager(
            RuntimeEnvironment.getApplication(),
            new Configuration.Builder().build()
        );
        MainActivity activity = Robolectric.buildActivity(MainActivity.class).create().get();
        ViewGroup content = activity.findViewById(android.R.id.content);
        LinearLayout root = (LinearLayout) content.getChildAt(0);
        LinearLayout topBar = (LinearLayout) root.getChildAt(0);

        ImageView logo = (ImageView) topBar.getChildAt(0);
        assertNotNull(logo.getDrawable());
        assertNotNull(findText(root, "Jarvis"));
        assertNotNull(findText(root, "What can I help with?"));
        assertNotNull(findText(root, "Type a message or tap the microphone."));
        assertNotNull(findByDescription(root, "House activity"));
        assertNotNull(findByDescription(root, "New chat"));

        ImageButton more = (ImageButton) findByDescription(root, "More options");
        assertNotNull(more);
        more.performClick();
        PopupMenu popup = ShadowPopupMenu.getLatestPopupMenu();
        assertNotNull(popup);
        Menu menu = popup.getMenu();
        assertEquals(
            List.of("Chat history", "Improvements", "Delete current chat", "Settings", "Developer mode"),
            List.of(
                menu.getItem(0).getTitle().toString(),
                menu.getItem(1).getTitle().toString(),
                menu.getItem(2).getTitle().toString(),
                menu.getItem(3).getTitle().toString(),
                menu.getItem(4).getTitle().toString()
            )
        );

        menu.performIdentifierAction(1, 0);
        Intent history = shadowOf(activity).getNextStartedActivity();
        assertNotNull(history);
        assertEquals(ChatHistoryActivity.class.getName(), history.getComponent().getClassName());

        more.performClick();
        ShadowPopupMenu.getLatestPopupMenu().getMenu().performIdentifierAction(4, 0);
        Intent improvements = shadowOf(activity).getNextStartedActivity();
        assertNotNull(improvements);
        assertEquals(ImprovementsActivity.class.getName(), improvements.getComponent().getClassName());

        more.performClick();
        ShadowPopupMenu.getLatestPopupMenu().getMenu().performIdentifierAction(3, 0);
        Intent settings = shadowOf(activity).getNextStartedActivity();
        assertNotNull(settings);
        assertEquals(SettingsActivity.class.getName(), settings.getComponent().getClassName());

        more.performClick();
        ShadowPopupMenu.getLatestPopupMenu().getMenu().performIdentifierAction(2, 0);
        AlertDialog delete = ShadowAlertDialog.getLatestAlertDialog();
        assertNotNull(delete);
        ShadowAlertDialog shadowDelete = Shadow.extract(delete);
        assertEquals("Delete this chat?", shadowDelete.getTitle());
        assertEquals(
            "This removes the current chat from the app and starts a fresh conversation.",
            shadowDelete.getMessage()
        );

        View developerAdd = findByDescription(root, "Add developer attachment or action");
        assertNotNull(developerAdd);
        assertEquals(View.GONE, developerAdd.getVisibility());
        TextView composer = findTextByHint(root, "Message Jarvis");
        assertNotNull(composer);
        assertTrue(composer.isShown() || composer.getVisibility() == View.VISIBLE);
    }

    private static View findByDescription(View root, String expected) {
        if (root.getContentDescription() != null
                && expected.contentEquals(root.getContentDescription())) return root;
        if (!(root instanceof ViewGroup group)) return null;
        for (int index = 0; index < group.getChildCount(); index++) {
            View found = findByDescription(group.getChildAt(index), expected);
            if (found != null) return found;
        }
        return null;
    }

    private static TextView findText(View root, String expected) {
        if (root instanceof TextView text && expected.contentEquals(text.getText())) return text;
        if (!(root instanceof ViewGroup group)) return null;
        for (int index = 0; index < group.getChildCount(); index++) {
            TextView found = findText(group.getChildAt(index), expected);
            if (found != null) return found;
        }
        return null;
    }

    private static TextView findTextByHint(View root, String expected) {
        if (root instanceof TextView text
                && text.getHint() != null
                && expected.contentEquals(text.getHint())) return text;
        if (!(root instanceof ViewGroup group)) return null;
        for (int index = 0; index < group.getChildCount(); index++) {
            TextView found = findTextByHint(group.getChildAt(index), expected);
            if (found != null) return found;
        }
        return null;
    }
}
