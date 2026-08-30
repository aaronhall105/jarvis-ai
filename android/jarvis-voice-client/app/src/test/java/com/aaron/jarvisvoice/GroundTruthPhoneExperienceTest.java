package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.robolectric.Shadows.shadowOf;

import android.app.Dialog;
import android.content.Intent;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;

import androidx.work.Configuration;
import androidx.work.WorkManager;

import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;
import org.robolectric.shadows.ShadowDialog;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35)
public final class GroundTruthPhoneExperienceTest {
    @Test public void mainScreenRendersTheForensicGroundTruthExperienceAndNavigation() {
        WorkManager.initialize(
            RuntimeEnvironment.getApplication(),
            new Configuration.Builder().build()
        );
        MainActivity activity = Robolectric.buildActivity(MainActivity.class).create().get();
        View root = activity.findViewById(android.R.id.content);

        assertNotNull(findText(root, "J A R V I S"));
        assertNotNull(findText(root, "What can I help with?"));
        assertNotNull(findText(root, "Type a message or tap the microphone."));
        assertNotNull(findDescription(root, "House activity"));
        assertNotNull(findDescription(root, "New chat"));
        assertNotNull(findDescription(root, "Clear current chat"));
        assertNotNull(findDescription(root, "Settings"));
        assertNotNull(findDescription(root, "Add attachment or action"));
        assertNotNull(findDescription(root, "Start voice"));
        assertNotNull(findDescription(root, "Send message"));

        findDescription(root, "Settings").performClick();
        Intent settings = shadowOf(activity).getNextStartedActivity();
        assertNotNull(settings);
        assertEquals(SettingsActivity.class.getName(), settings.getComponent().getClassName());

        findDescription(root, "Add attachment or action").performClick();
        Dialog actions = ShadowDialog.getLatestDialog();
        assertNotNull(actions);
        View actionRoot = actions.findViewById(android.R.id.content);
        assertNotNull(findText(actionRoot, "Chat history"));
        assertNotNull(findText(actionRoot, "Improvements"));
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

    private static View findDescription(View root, String expected) {
        CharSequence description = root.getContentDescription();
        if (description != null && expected.contentEquals(description)) return root;
        if (!(root instanceof ViewGroup group)) return null;
        for (int index = 0; index < group.getChildCount(); index++) {
            View found = findDescription(group.getChildAt(index), expected);
            if (found != null) return found;
        }
        return null;
    }
}
