package com.aaron.jarvisvoice;

import android.content.Context;
import android.content.SharedPreferences;

public final class UpdatePreferences {
    private final Context context;
    private final SharedPreferences values;
    public UpdatePreferences(Context context) {
        this.context = context.getApplicationContext();
        values = this.context.getSharedPreferences("jarvis_updates", Context.MODE_PRIVATE);
    }
    public UpdateChannel channel() { return UpdateChannel.parse(values.getString("channel", "ALPHA")); }
    public void setChannel(UpdateChannel value) { values.edit().putString("channel", value.name()).apply(); }
    public boolean automaticChecks() { return values.getBoolean("automatic_checks", true); }
    public void setAutomaticChecks(boolean value) { values.edit().putBoolean("automatic_checks", value).apply(); }
    public boolean wifiAutoDownload() { return values.getBoolean("wifi_auto_download", false); }
    public void setWifiAutoDownload(boolean value) { values.edit().putBoolean("wifi_auto_download", value).apply(); }
    public long lastChecked() { return values.getLong("last_checked", 0); }
    public void setLastChecked(long value) { values.edit().putLong("last_checked", value).apply(); }
    public String previousVersion() { return values.getString("previous_version", "Unknown"); }
    public long lastSuccessfulUpdate() { return values.getLong("last_successful_update", 0); }
    public String availableManifest() { return values.getString("available_manifest", ""); }
    public void setAvailableManifest(String value) { values.edit().putString("available_manifest", value).apply(); }
    public String verifiedManifest() { return values.getString("verified_manifest", ""); }
    public void setVerifiedManifest(String value) { values.edit().putString("verified_manifest", value).apply(); }
    public String installedVerifiedManifest() { return values.getString("installed_verified_manifest", ""); }
    public String error() { return values.getString("error", ""); }
    public void setError(String value) { values.edit().putString("error", value).apply(); }
    public void recordLaunch(String current) {
        String seen = values.getString("last_seen_version", "");
        if (!seen.isBlank() && !seen.equals(current)) {
            String installedVerification = matchingVerifiedManifest(current);
            values.edit()
                .putString("previous_version", seen)
                .putLong("last_successful_update", System.currentTimeMillis())
                .putString("last_seen_version", current)
                .putString("verified_manifest", "")
                .putString("installed_verified_manifest", installedVerification)
                .apply();
        }
        else if (seen.isBlank()) values.edit().putString("last_seen_version", current).apply();
    }

    private String matchingVerifiedManifest(String current) {
        String manifest = verifiedManifest();
        try {
            UpdateRelease release = UpdateManager.stored(manifest);
            return release != null
                && release.versionName().equals(current)
                && release.versionCode() == currentVersionCode()
                ? manifest
                : "";
        } catch (Exception ignored) {
            return "";
        }
    }

    private long currentVersionCode() throws android.content.pm.PackageManager.NameNotFoundException {
        return context.getPackageManager()
            .getPackageInfo(context.getPackageName(), 0)
            .getLongVersionCode();
    }
}
