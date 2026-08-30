package com.aaron.jarvisvoice;

import org.junit.Test;
import static org.junit.Assert.*;

public final class UpdateReleaseTest {
    private static String manifest(String version, long code, String channel, String url, String sha) {
        return "{\"schemaVersion\":1,\"versionName\":\"" + version + "\",\"versionCode\":" + code
            + ",\"channel\":\"" + channel + "\",\"apkUrl\":\"" + url + "\",\"sha256\":\"" + sha
            + "\",\"apkSize\":1234,\"releaseNotes\":\"Test notes\",\"publishedAt\":\"2026-08-20T12:00:00Z\""
            + ",\"minSdk\":31,\"minRealtimeProtocol\":2,\"commitSha\":\"0123456789abcdef0123456789abcdef01234567\""
            + ",\"tag\":\"v" + version + "\"}";
    }
    private static String valid(String version, long code, String channel) { return manifest(version, code, channel, "https://github.com/aaronhall105/jarvis-ai/releases/download/v" + version + "/jarvis.apk", "a".repeat(64)); }

    @Test public void semanticOrdering() {
        String[] versions = {"19.0.0-alpha15", "19.0.0-alpha16", "19.0.0-beta1", "19.0.0", "19.1.0-alpha1"};
        for (int i = 1; i < versions.length; i++) assertTrue(SemanticVersion.parse(versions[i]).compareTo(SemanticVersion.parse(versions[i - 1])) > 0);
        assertTrue(SemanticVersion.parse("19.0.0-beta2").compareTo(SemanticVersion.parse("19.0.0-beta1")) > 0);
    }
    @Test public void channelEligibility() {
        assertTrue(UpdateChannel.STABLE.accepts(UpdateChannel.STABLE));
        assertFalse(UpdateChannel.STABLE.accepts(UpdateChannel.BETA)); assertFalse(UpdateChannel.STABLE.accepts(UpdateChannel.ALPHA));
        assertTrue(UpdateChannel.BETA.accepts(UpdateChannel.STABLE)); assertTrue(UpdateChannel.BETA.accepts(UpdateChannel.BETA)); assertFalse(UpdateChannel.BETA.accepts(UpdateChannel.ALPHA));
        assertTrue(UpdateChannel.ALPHA.accepts(UpdateChannel.STABLE)); assertTrue(UpdateChannel.ALPHA.accepts(UpdateChannel.BETA)); assertTrue(UpdateChannel.ALPHA.accepts(UpdateChannel.ALPHA));
    }
    @Test public void currentAndOlderAreNeverOffered() {
        assertFalse(UpdateRelease.parse(valid("19.0.0-alpha15", 190150, "alpha")).isEligible(UpdateChannel.ALPHA, "19.0.0-alpha15", 190150, 36, 2));
        assertFalse(UpdateRelease.parse(valid("19.0.0-alpha14", 190140, "alpha")).isEligible(UpdateChannel.ALPHA, "19.0.0-alpha15", 190150, 36, 2));
        assertFalse(UpdateRelease.parse(valid("19.0.0-alpha16", 190140, "alpha")).isEligible(UpdateChannel.ALPHA, "19.0.0-alpha15", 190150, 36, 2));
        assertTrue(UpdateRelease.parse(valid("19.0.0-alpha16", 190160, "alpha")).isEligible(UpdateChannel.ALPHA, "19.0.0-alpha15", 190150, 36, 2));
    }
    @Test public void malformedManifestRejected() {
        assertThrows(IllegalArgumentException.class, () -> UpdateRelease.parse("{"));
        assertThrows(IllegalArgumentException.class, () -> UpdateRelease.parse("{}"));
        assertThrows(IllegalArgumentException.class, () -> UpdateRelease.parse(valid("19.0.0-alpha16", 190160, "alpha").replace("\"schemaVersion\":1", "\"schemaVersion\":2")));
    }
    @Test public void unsafeUrlAndChecksumRejected() {
        assertThrows(IllegalArgumentException.class, () -> UpdateRelease.parse(manifest("19.0.0-alpha16", 190160, "alpha", "http://example.com/a.apk", "a".repeat(64))));
        assertThrows(IllegalArgumentException.class, () -> UpdateRelease.parse(manifest("19.0.0-alpha16", 190160, "alpha", "https://user:pass@example.com/a.apk", "a".repeat(64))));
        assertThrows(IllegalArgumentException.class, () -> UpdateRelease.parse(manifest("19.0.0-alpha16", 190160, "alpha", "https://example.com/a.apk", "bad")));
    }
    @Test public void wrongChannelRejected() {
        assertThrows(IllegalArgumentException.class, () -> UpdateRelease.parse(valid("19.0.0-alpha16", 190160, "beta")));
        assertThrows(IllegalArgumentException.class, () -> UpdateChannel.parse("nightly"));
    }

    @Test public void channelFeedsUseReleaseAssetsNotADevelopmentBranch() {
        assertEquals(
            "https://github.com/aaronhall105/jarvis-ai/releases/download/jarvis-alpha-feed/update-manifest.json",
            UpdateManager.feedUrl(UpdateChannel.ALPHA)
        );
        assertFalse(UpdateManager.feedUrl(UpdateChannel.ALPHA).contains("ota-feeds"));
    }
}
