package com.aaron.jarvisvoice;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.os.Bundle;
import android.text.format.DateFormat;
import android.view.*;
import android.widget.*;
import java.io.File;
import java.util.Date;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class UpdatesActivity extends Activity {
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private UpdatePreferences prefs;
    private UpdateManager updates;
    private TextView status, available, notes, integrity, history;
    private ProgressBar progress;
    private Button check, download, install;
    private Spinner channel;
    private Switch automatic, wifi;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state); prefs = new UpdatePreferences(this); updates = new UpdateManager(this);
        setTitle("Jarvis Updates"); setContentView(content()); refresh();
    }
    @Override protected void onResume() { super.onResume(); refresh(); }
    @Override protected void onDestroy() { executor.shutdownNow(); super.onDestroy(); }

    private View content() {
        LinearLayout page = new LinearLayout(this); page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(dp(20), dp(20), dp(20), dp(36)); page.setBackgroundColor(Color.WHITE);
        title(page, "Jarvis Updates", 27); label(page, "CURRENT VERSION"); title(page, JarvisVersion.RELEASE, 18);
        label(page, "UPDATE CHANNEL");
        channel = new Spinner(this); channel.setAdapter(new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item, new String[]{"Alpha", "Beta", "Stable"}));
        channel.setSelection(switch (prefs.channel()) { case ALPHA -> 0; case BETA -> 1; case STABLE -> 2; });
        channel.setOnItemSelectedListener(new android.widget.AdapterView.OnItemSelectedListener() {
            public void onNothingSelected(android.widget.AdapterView<?> parent) {}
            public void onItemSelected(android.widget.AdapterView<?> parent, View view, int position, long id) {
                prefs.setChannel(position == 0 ? UpdateChannel.ALPHA : position == 1 ? UpdateChannel.BETA : UpdateChannel.STABLE); refresh();
            }}); page.addView(channel);
        label(page, "AUTOMATIC UPDATES"); automatic = toggle(page, "Check automatically", prefs.automaticChecks());
        wifi = toggle(page, "Download automatically on Wi-Fi", prefs.wifiAutoDownload());
        automatic.setOnCheckedChangeListener((v, checked) -> { prefs.setAutomaticChecks(checked); UpdateManager.schedule(this); });
        wifi.setOnCheckedChangeListener((v, checked) -> prefs.setWifiAutoDownload(checked));
        label(page, "STATUS"); status = text(page, "Not checked yet"); progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100); progress.setVisibility(View.GONE); page.addView(progress, match());
        label(page, "AVAILABLE UPDATE"); available = text(page, "None"); notes = text(page, "");
        download = button(page, "Download Update", this::download); install = button(page, "Install Update", this::install); check = button(page, "Check for updates", this::check);
        label(page, "INTEGRITY"); integrity = text(page, "Not verified");
        label(page, "PREVIOUS VERSION / ROLLBACK INFO"); history = text(page, "");
        ScrollView scroll = new ScrollView(this); scroll.addView(page); return scroll;
    }

    private void check(View ignored) {
        busy("Checking for updates…"); executor.execute(() -> {
            try { UpdateRelease release = updates.check(); runOnUiThread(() -> { status.setText(release == null ? "Up to date" : "Update available"); refresh(); }); }
            catch (Exception exception) { error(exception); }
        });
    }
    private void download(View ignored) {
        UpdateRelease release = stored(prefs.availableManifest()); if (release == null) return;
        busy("Downloading…"); progress.setVisibility(View.VISIBLE); executor.execute(() -> {
            try { updates.download(release, (received, total) -> runOnUiThread(() -> progress.setProgress((int)(received * 100 / total))));
                runOnUiThread(() -> { progress.setVisibility(View.GONE); status.setText("Ready to install"); refresh(); });
            } catch (Exception exception) { runOnUiThread(() -> progress.setVisibility(View.GONE)); error(exception); }
        });
    }
    private void install(View ignored) {
        try {
            UpdateRelease release = stored(prefs.verifiedManifest()); if (release == null) throw new IllegalStateException("No verified update is ready");
            File apk = new File(getFilesDir(), "updates/verified/jarvis-update.apk"); ApkVerifier.verify(this, apk, release);
            if (!ApkInstaller.hasPermission(this)) { startActivity(ApkInstaller.permissionIntent(this)); status.setText("Allow Jarvis to install unknown apps, then return here"); return; }
            ApkInstaller.install(this, apk);
        } catch (Exception exception) { error(exception); }
    }
    private void refresh() {
        UpdateRelease release = stored(prefs.availableManifest()); UpdateRelease verified = stored(prefs.verifiedManifest());
        String checked = prefs.lastChecked() == 0 ? "Never" : DateFormat.getMediumDateFormat(this).format(new Date(prefs.lastChecked())) + " " + DateFormat.getTimeFormat(this).format(new Date(prefs.lastChecked()));
        status.setText(prefs.error().isBlank() ? "Last checked: " + checked : "Error: " + prefs.error());
        if (release == null) { available.setText("None"); notes.setText(""); } else {
            available.setText(release.versionName() + " • " + formatSize(release.apkSize()) + " • " + release.publishedAt().substring(0, 10)); notes.setText(release.releaseNotes());
        }
        download.setEnabled(release != null); install.setEnabled(verified != null); integrity.setText(verified == null ? "Not verified" : "Verified: checksum, package, version and signing certificate");
        String updated = prefs.lastSuccessfulUpdate() == 0 ? "Unknown" : java.text.DateFormat.getDateTimeInstance().format(new Date(prefs.lastSuccessfulUpdate()));
        history.setText("Previous installed version: " + prefs.previousVersion() + "\nValidated pre-OTA baseline: 19.0.0-alpha14\nLast successful update: " + updated + "\n\nAndroid normally blocks installing a lower versionCode. Safe rollback requires a recovery build containing known-good code with a new, higher versionCode; Jarvis never uninstalls itself or wipes settings.");
        check.setEnabled(true);
    }
    private void busy(String value) { status.setText(value); check.setEnabled(false); download.setEnabled(false); install.setEnabled(false); }
    private void error(Exception exception) { runOnUiThread(() -> { status.setText("Error: " + (exception.getMessage() == null ? "Update failed" : exception.getMessage())); refresh(); }); }
    private UpdateRelease stored(String value) { try { return UpdateManager.stored(value); } catch (Exception ignored) { return null; } }
    private Switch toggle(LinearLayout page, String title, boolean checked) { Switch value = new Switch(this); value.setText(title); value.setChecked(checked); page.addView(value, match()); return value; }
    private Button button(LinearLayout page, String title, View.OnClickListener listener) { Button value = new Button(this); value.setText(title); value.setOnClickListener(listener); page.addView(value, match()); return value; }
    private void label(LinearLayout page, String value) { TextView view = text(page, value); view.setTextColor(Color.rgb(90,90,90)); view.setPadding(0, dp(22), 0, dp(5)); }
    private void title(LinearLayout page, String value, int size) { TextView view = text(page, value); view.setTextSize(size); }
    private TextView text(LinearLayout page, String value) { TextView view = new TextView(this); view.setText(value); view.setTextSize(15); view.setTextColor(Color.rgb(20,20,20)); page.addView(view, match()); return view; }
    private LinearLayout.LayoutParams match() { return new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT); }
    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private static String formatSize(long bytes) { return String.format(java.util.Locale.ROOT, "%.1f MB", bytes / 1048576.0); }
}
