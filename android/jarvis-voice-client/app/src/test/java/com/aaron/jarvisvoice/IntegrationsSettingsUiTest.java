package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertFalse;
import static org.robolectric.Shadows.shadowOf;

import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.net.Uri;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;

import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35)
public final class IntegrationsSettingsUiTest {
    private static final List<String> SECTION_ORDER = List.of(
        "Voice and conversation",
        "Wake word and background",
        "Assistant and overlay",
        "Connections",
        "Integrations",
        "Developer",
        "Updates",
        "Diagnostics"
    );

    @Test public void settingsExposeTopLevelIntegrationsAndLaunchExistingActivity() {
        SettingsActivity activity = Robolectric.buildActivity(SettingsActivity.class).create().get();
        View root = activity.findViewById(android.R.id.content);

        assertEquals(SECTION_ORDER, sectionHeadings(root));
        View section = root.findViewWithTag(SettingsActivity.INTEGRATIONS_SECTION_TAG);
        View content = root.findViewWithTag(SettingsActivity.INTEGRATIONS_CONTENT_TAG);
        View open = root.findViewWithTag(SettingsActivity.INTEGRATIONS_BUTTON_TAG);
        assertNotNull(section);
        assertNotNull(content);
        assertNotNull(open);
        assertEquals(View.GONE, content.getVisibility());
        assertNotNull(findText(root, "Google, email, calendar, contacts and external services."));

        ((ViewGroup) section).getChildAt(0).performClick();
        assertEquals(View.VISIBLE, content.getVisibility());
        open.performClick();

        Intent launched = shadowOf(activity).getNextStartedActivity();
        assertNotNull(launched);
        assertEquals(IntegrationsActivity.class.getName(), launched.getComponent().getClassName());
    }

    @Test public void integrationsActivityAndGoogleDeepLinkAreDeclaredAndResolvable() {
        SettingsActivity activity = Robolectric.buildActivity(SettingsActivity.class).create().get();
        Intent deepLink = new Intent(Intent.ACTION_VIEW, Uri.parse("jarvis://integrations/google"))
            .addCategory(Intent.CATEGORY_BROWSABLE)
            .setPackage(activity.getPackageName());

        ResolveInfo resolved = activity.getPackageManager().resolveActivity(
            deepLink,
            PackageManager.MATCH_DEFAULT_ONLY
        );

        assertNotNull(resolved);
        assertEquals("com.aaron.jarvisvoice", resolved.activityInfo.packageName);
        assertEquals(IntegrationsActivity.class.getName(), resolved.activityInfo.name);
    }

    @Test public void setupRequiredAndDisconnectedProvidersNeverRenderAsConnected() {
        IntegrationsActivity activity = Robolectric.buildActivity(IntegrationsActivity.class)
            .create()
            .get();
        activity.renderProviders(List.of(
            provider("google", "Google", "Setup required"),
            provider("gmail", "Gmail", "Not connected"),
            provider("calendar", "Calendar", "Not connected"),
            provider("contacts", "Contacts", "Not connected")
        ));

        View root = activity.findViewById(android.R.id.content);
        assertNotNull(findText(root, "Google"));
        assertNotNull(findText(root, "Setup required"));
        assertNotNull(findText(root, "Gmail"));
        assertNotNull(findText(root, "Calendar"));
        assertNotNull(findText(root, "Contacts"));
        assertEquals(3, countText(root, "Not connected"));
        assertNull(findText(root, "Connected"));
    }

    @Test public void authenticationRejectionIsNeverRenderedAsCoreOffline() {
        IntegrationsActivity activity = Robolectric.buildActivity(IntegrationsActivity.class)
            .create()
            .get();
        activity.showProviderFailure(new IntegrationsClient.Failure(
            IntegrationsClient.FailureKind.AUTHENTICATION_REJECTED,
            "Jarvis Core rejected the mobile voice token"
        ));

        View root = activity.findViewById(android.R.id.content);
        assertNotNull(findText(
            root,
            "Core authentication rejected — check the mobile voice token in Settings"
        ));
        assertEquals(11, countText(root, "Authentication required"));
        assertFalse(allText(root).contains("Core offline"));
    }

    private static IntegrationProvider provider(String id, String name, String state) {
        return new IntegrationProvider(
            id,
            name,
            state,
            false,
            false,
            "Provider access has not been verified",
            List.of(),
            List.of(),
            "",
            "",
            false,
            false,
            false
        );
    }

    private static List<String> sectionHeadings(View root) {
        Set<String> expected = Set.copyOf(SECTION_ORDER);
        List<String> found = new ArrayList<>();
        collectSectionHeadings(root, expected, found);
        return found;
    }

    private static void collectSectionHeadings(View view, Set<String> expected, List<String> found) {
        if (view instanceof TextView text && expected.contains(text.getText().toString())) {
            found.add(text.getText().toString());
        }
        if (!(view instanceof ViewGroup group)) return;
        for (int index = 0; index < group.getChildCount(); index++) {
            collectSectionHeadings(group.getChildAt(index), expected, found);
        }
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

    private static int countText(View root, String expected) {
        int count = root instanceof TextView text && expected.contentEquals(text.getText()) ? 1 : 0;
        if (!(root instanceof ViewGroup group)) return count;
        for (int index = 0; index < group.getChildCount(); index++) {
            count += countText(group.getChildAt(index), expected);
        }
        return count;
    }

    private static String allText(View root) {
        StringBuilder value = new StringBuilder();
        collectText(root, value);
        return value.toString();
    }

    private static void collectText(View view, StringBuilder value) {
        if (view instanceof TextView text) value.append(text.getText()).append('\n');
        if (!(view instanceof ViewGroup group)) return;
        for (int index = 0; index < group.getChildCount(); index++) {
            collectText(group.getChildAt(index), value);
        }
    }
}
