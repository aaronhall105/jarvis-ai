package com.aaron.jarvisvoice;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import android.content.Context;
import android.content.SharedPreferences;

import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35)
public final class SecureStoreMigrationTest {
    @Test public void legacyEncryptedMobileSettingsSurviveAnInPlaceUpgrade() {
        Context context = RuntimeEnvironment.getApplication();
        SharedPreferences preferences = context.getSharedPreferences(
            "jarvis_voice_settings",
            Context.MODE_PRIVATE
        );
        preferences.edit()
            .clear()
            .putString("token", "encrypted-envelope-placeholder")
            .putString("base_url", "http://192.168.1.40:8000")
            .commit();

        SecureStore store = new SecureStore(context);

        assertTrue(store.hasMobileToken());
        assertEquals("http://192.168.1.40:8000", store.coreUrl());
    }
}
