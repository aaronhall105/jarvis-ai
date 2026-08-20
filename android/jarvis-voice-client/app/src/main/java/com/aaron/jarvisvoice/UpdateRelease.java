package com.aaron.jarvisvoice;

import org.json.JSONObject;
import java.net.URI;
import java.time.Instant;
import java.util.Locale;

public record UpdateRelease(
    int schemaVersion, String versionName, long versionCode, UpdateChannel channel,
    String apkUrl, String sha256, long apkSize, String releaseNotes,
    String publishedAt, int minSdk, int minRealtimeProtocol, String commitSha, String tag
) {
    public static UpdateRelease parse(String json) {
        try {
            JSONObject root = new JSONObject(json);
            int schema = root.getInt("schemaVersion");
            if (schema != 1) throw new IllegalArgumentException("Unsupported update manifest schema");
            UpdateRelease release = new UpdateRelease(
                schema, root.getString("versionName"), root.getLong("versionCode"),
                UpdateChannel.parse(root.getString("channel")), root.getString("apkUrl"),
                root.getString("sha256").toLowerCase(Locale.ROOT), root.getLong("apkSize"),
                root.getString("releaseNotes"), root.getString("publishedAt"),
                root.getInt("minSdk"), root.getInt("minRealtimeProtocol"),
                root.getString("commitSha"), root.getString("tag")
            );
            release.validate();
            return release;
        } catch (IllegalArgumentException exception) { throw exception; }
        catch (Exception exception) { throw new IllegalArgumentException("Malformed update manifest", exception); }
    }

    public void validate() {
        SemanticVersion parsed = SemanticVersion.parse(versionName);
        if (parsed.channel != channel) throw new IllegalArgumentException("Manifest channel does not match version");
        URI uri;
        try { uri = URI.create(apkUrl); } catch (Exception exception) { throw new IllegalArgumentException("Invalid APK URL"); }
        if (!"https".equalsIgnoreCase(uri.getScheme()) || uri.getHost() == null || uri.getUserInfo() != null)
            throw new IllegalArgumentException("APK URL must be credential-free HTTPS");
        if (!sha256.matches("[0-9a-f]{64}")) throw new IllegalArgumentException("Invalid SHA-256");
        if (apkSize <= 0 || apkSize > 500L * 1024 * 1024) throw new IllegalArgumentException("Invalid APK size");
        if (versionCode <= 0 || minSdk <= 0 || minRealtimeProtocol <= 0) throw new IllegalArgumentException("Invalid release compatibility");
        Instant.parse(publishedAt);
        if (!commitSha.matches("[0-9a-fA-F]{40}")) throw new IllegalArgumentException("Invalid commit SHA");
        if (!tag.equals("v" + versionName)) throw new IllegalArgumentException("Release tag mismatch");
    }

    public boolean isEligible(UpdateChannel selected, String currentName, long currentCode, int sdk, int protocol) {
        return selected.accepts(channel)
            && versionCode > currentCode
            && SemanticVersion.parse(versionName).compareTo(SemanticVersion.parse(currentName)) > 0
            && minSdk <= sdk && minRealtimeProtocol <= protocol;
    }
}
