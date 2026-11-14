package com.aaron.jarvisvoice;

import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;
import org.json.JSONObject;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.LooperMode;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.shadows.ShadowToast;
import static org.junit.Assert.*;

/** Read-only UI tests: no provider, signing key, network service or task activation. */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35)
@LooperMode(LooperMode.Mode.PAUSED)
public class AutonomyInteractionUiTest {
    @Test public void reviewTapDuringObservationIsNotDroppedAndShowsLoading() throws Exception {
        try (ActivityController<AutonomyActivity> screen = Robolectric.buildActivity(AutonomyActivity.class).create()) {
            AutonomyActivity activity = screen.get();
            set(activity, "state", new JSONObject().put("status", "STOPPED"));
            set(activity, "observing", true);
            invoke(activity, "loadProduct", new Class<?>[]{String.class, String.class}, "prepare", "80");
            assertEquals(true, get(activity, "busy"));
            assertEquals(true, get(activity, "productLoading"));
            assertTrue(labels(activity).contains("LOADING PROTECTED REVIEW"));
            assertFalse(labels(activity).contains("Prepare this exact task"));
        }
    }

    @Test public void duplicateTapDuringProtectedOperationIsExplained() throws Exception {
        try (ActivityController<AutonomyActivity> screen = Robolectric.buildActivity(AutonomyActivity.class).create()) {
            AutonomyActivity activity = screen.get();
            set(activity, "busy", true);
            invoke(activity, "loadProduct", new Class<?>[]{String.class, String.class}, "prepare", "80");
            assertEquals("A protected request is already in progress.", ShadowToast.getTextOfLatestToast());
        }
    }

    @Test public void preparationErrorIsBesideRequestNotBelowWholeDashboard() throws Exception {
        try (ActivityController<AutonomyActivity> screen = Robolectric.buildActivity(AutonomyActivity.class).create()) {
            AutonomyActivity activity = screen.get();
            set(activity, "state", new JSONObject().put("status", "STOPPED"));
            set(activity, "productKind", "prepare");
            set(activity, "productError", "Protected review unavailable");
            invoke(activity, "render", new Class<?>[]{String.class}, (Object) null);
            List<String> labels = labels(activity);
            assertTrue(labels.indexOf("PROTECTED REQUEST NEEDS ATTENTION") >= 0);
            assertTrue(labels.indexOf("PROTECTED REQUEST NEEDS ATTENTION")
                < labels.indexOf("AUTONOMOUS DEVELOPMENT STOPPED"));
            assertTrue(labels.contains("Retry read-only review"));
            assertFalse(labels.contains("Prepare this exact task"));
        }
    }

    @Test public void automaticRefreshDoesNotReplaceAnOpenReview() throws Exception {
        try (ActivityController<AutonomyActivity> screen = Robolectric.buildActivity(AutonomyActivity.class).create()) {
            AutonomyActivity activity = screen.get();
            set(activity, "productPreview", new JSONObject());
            ((Runnable) get(activity, "refreshLoop")).run();
            assertEquals(false, get(activity, "observing"));
        }
    }

    private static Object get(AutonomyActivity activity, String name) throws Exception {
        Field field = AutonomyActivity.class.getDeclaredField(name);
        field.setAccessible(true);
        return field.get(activity);
    }
    private static void set(AutonomyActivity activity, String name, Object value) throws Exception {
        Field field = AutonomyActivity.class.getDeclaredField(name);
        field.setAccessible(true);
        field.set(activity, value);
    }
    private static void invoke(AutonomyActivity activity, String name, Class<?>[] types, Object... args) throws Exception {
        Method method = AutonomyActivity.class.getDeclaredMethod(name, types);
        method.setAccessible(true);
        method.invoke(activity, args);
    }
    private static List<String> labels(AutonomyActivity activity) {
        List<String> labels = new ArrayList<>();
        collect(activity.findViewById(android.R.id.content), labels);
        return labels;
    }
    private static void collect(View view, List<String> labels) {
        if (view instanceof TextView) labels.add(((TextView) view).getText().toString());
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++) collect(group.getChildAt(i), labels);
        }
    }
}
