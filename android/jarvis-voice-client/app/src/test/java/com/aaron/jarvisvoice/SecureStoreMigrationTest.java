package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import android.content.Context;
import android.content.SharedPreferences;

import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35)
public final class SecureStoreMigrationTest {
    private SharedPreferences preferences;

    @Before public void setUp() {
        preferences = RuntimeEnvironment.getApplication().getSharedPreferences(
            "jarvis_voice_settings", Context.MODE_PRIVATE
        );
        preferences.edit().clear().commit();
    }

    @After public void tearDown() {
        preferences.edit().clear().commit();
    }

    @Test public void legacyEncryptedMobileSettingsSurviveAnInPlaceUpgrade() {
        Context context = RuntimeEnvironment.getApplication();
        preferences.edit()
            .clear()
            .putString("token", "encrypted-envelope-placeholder")
            .putString("base_url", "http://192.168.1.40:8000")
            .commit();

        SecureStore store = new SecureStore(context);

        assertTrue(store.hasMobileToken());
        assertEquals("http://192.168.1.40:8000", store.coreUrl());
    }

    @Test public void configuredRemoteRouteIsSufficientWithoutLanOrAuthReset() {
        preferences.edit()
            .putBoolean("remote_core_migration_v190140", true)
            .putString("remote_core_url_v190140", "https://jarvis.example.ts.net")
            .putString("mobile_voice_token", "opaque-encrypted-token")
            .commit();

        SecureStore store = new SecureStore(RuntimeEnvironment.getApplication());

        assertTrue(store.hasConfiguredCoreEndpoint());
        assertTrue(store.hasMobileToken());
        assertEquals("", store.coreUrl());
        assertEquals("https://jarvis.example.ts.net", store.remoteCoreUrl());
    }
}
