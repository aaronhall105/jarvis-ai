package com.aaron.jarvisvoice;

import android.content.Context;
import android.content.pm.PackageInfo;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;

import static org.junit.Assert.*;
import static org.robolectric.Shadows.shadowOf;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 35)
public final class UpdatePreferencesTest {
    private Context context;

    @Before public void setUp() {
        context = RuntimeEnvironment.getApplication();
        context.getSharedPreferences("jarvis_updates", Context.MODE_PRIVATE)
            .edit().clear().commit();
    }

    @Test public void successfulOtaPromotesMatchingVerificationRecord() {
        UpdatePreferences preferences = new UpdatePreferences(context);
        String manifest = manifest("19.0.0-alpha24", 190260);
        preferences.recordLaunch("19.0.0-alpha23");
        preferences.setVerifiedManifest(manifest);
        installPackageVersion("19.0.0-alpha24", 190260);

        preferences.recordLaunch("19.0.0-alpha24");

        assertEquals("19.0.0-alpha23", preferences.previousVersion());
        assertEquals("", preferences.verifiedManifest());
        assertEquals(manifest, preferences.installedVerifiedManifest());
        assertTrue(preferences.lastSuccessfulUpdate() > 0L);
    }

    @Test public void unrelatedVerificationRecordIsNotPromoted() {
        UpdatePreferences preferences = new UpdatePreferences(context);
        preferences.recordLaunch("19.0.0-alpha23");
        preferences.setVerifiedManifest(manifest("19.0.0-alpha25", 190270));
        installPackageVersion("19.0.0-alpha24", 190260);

        preferences.recordLaunch("19.0.0-alpha24");

        assertEquals("", preferences.verifiedManifest());
        assertEquals("", preferences.installedVerifiedManifest());
    }

    @Test public void integritySummaryDistinguishesStagedInstalledAndUnverified() {
        UpdateRelease staged = UpdateRelease.parse(manifest("19.0.0-alpha25", 190270));
        UpdateRelease installed = UpdateRelease.parse(manifest("19.0.0-alpha24", 190260));
        assertTrue(UpdatesActivity.integritySummary(staged, null, "19.0.0-alpha24")
            .startsWith("Verified update ready"));
        assertTrue(UpdatesActivity.integritySummary(null, installed, "19.0.0-alpha24")
            .startsWith("Installed OTA verified"));
        assertTrue(UpdatesActivity.integritySummary(null, installed, "19.0.0-alpha23")
            .startsWith("Not verified"));
    }

    private static String manifest(String version, long code) {
        return "{\"schemaVersion\":1,\"versionName\":\"" + version
            + "\",\"versionCode\":" + code
            + ",\"channel\":\"alpha\",\"apkUrl\":\"https://github.com/aaronhall105/jarvis-ai/releases/download/v"
            + version + "/jarvis.apk\",\"sha256\":\"" + "a".repeat(64)
            + "\",\"apkSize\":1234,\"releaseNotes\":\"Test\",\"publishedAt\":\"2026-08-30T12:00:00Z\""
            + ",\"minSdk\":31,\"minRealtimeProtocol\":2,\"commitSha\":\"0123456789abcdef0123456789abcdef01234567\""
            + ",\"tag\":\"v" + version + "\"}";
    }

    private void installPackageVersion(String version, int code) {
        PackageInfo info = new PackageInfo();
        info.packageName = context.getPackageName();
        info.versionName = version;
        info.versionCode = code;
        shadowOf(context.getPackageManager()).installPackage(info);
    }
}
