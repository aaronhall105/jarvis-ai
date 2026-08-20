package com.aaron.jarvisvoice;

import android.content.Context;
import android.os.Build;
import androidx.work.*;
import okhttp3.*;
import java.io.*;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.Locale;
import java.util.concurrent.TimeUnit;

public final class UpdateManager {
    public interface Progress { void onProgress(long received, long total); }
    private static final String OWNER = "aaronhall105/jarvis-ai";
    private static final long MAX_APK = 500L * 1024 * 1024;
    private final Context context;
    private final UpdatePreferences preferences;
    private final OkHttpClient http = new OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS).readTimeout(60, TimeUnit.SECONDS)
        .followRedirects(true).build();

    public UpdateManager(Context context) {
        this.context = context.getApplicationContext();
        preferences = new UpdatePreferences(context);
    }

    public static String feedUrl(UpdateChannel channel) {
        return "https://raw.githubusercontent.com/" + OWNER + "/ota-feeds/feeds/"
            + channel.name().toLowerCase(Locale.ROOT) + ".json";
    }

    public UpdateRelease check() throws IOException {
        Request request = new Request.Builder().url(feedUrl(preferences.channel()))
            .header("Accept", "application/json").header("User-Agent", "Jarvis-Android-Updater").build();
        try (Response response = http.newCall(request).execute()) {
            if (!response.isSuccessful()) throw new IOException(httpError(response.code()));
            ResponseBody body = response.body();
            if (body == null || body.contentLength() > 1024 * 1024) throw new IOException("Invalid update response");
            String json = body.string();
            UpdateRelease release = UpdateRelease.parse(json);
            preferences.setLastChecked(System.currentTimeMillis());
            preferences.setError("");
            if (release.isEligible(preferences.channel(), JarvisVersion.RELEASE, currentVersionCode(),
                    Build.VERSION.SDK_INT, JarvisVersion.REALTIME_PROTOCOL)) {
                preferences.setAvailableManifest(json);
                return release;
            }
            preferences.setAvailableManifest("");
            return null;
        } catch (IllegalArgumentException exception) {
            preferences.setError(exception.getMessage());
            throw new IOException(exception.getMessage(), exception);
        } catch (IOException exception) {
            preferences.setError(safeMessage(exception)); throw exception;
        }
    }

    public File download(UpdateRelease release, Progress progress) throws IOException {
        release.validate();
        if (!preferences.channel().accepts(release.channel()))
            throw new SecurityException("Release is not eligible for the selected update channel");
        if (!release.isEligible(preferences.channel(), JarvisVersion.RELEASE, currentVersionCode(),
                Build.VERSION.SDK_INT, JarvisVersion.REALTIME_PROTOCOL))
            throw new SecurityException("Release is not a newer compatible Jarvis update");
        File root = new File(context.getFilesDir(), "updates");
        File partial = new File(root, "download.partial");
        File verifiedDir = new File(root, "verified");
        File verified = new File(verifiedDir, "jarvis-update.apk");
        if (!root.mkdirs() && !root.isDirectory()) throw new IOException("Update storage unavailable");
        Request request = new Request.Builder().url(release.apkUrl())
            .header("Accept", "application/vnd.android.package-archive")
            .header("User-Agent", "Jarvis-Android-Updater").build();
        try (Response response = http.newCall(request).execute()) {
            if (!response.isSuccessful()) throw new IOException(httpError(response.code()));
            ResponseBody body = response.body();
            if (body == null) throw new IOException("Empty APK response");
            long declared = body.contentLength();
            if (declared > MAX_APK || (declared >= 0 && declared != release.apkSize()))
                throw new IOException("APK download size does not match manifest");
            MessageDigest digest;
            try { digest = MessageDigest.getInstance("SHA-256"); }
            catch (Exception exception) { throw new IOException("SHA-256 unavailable", exception); }
            long received = 0;
            try (InputStream input = body.byteStream(); OutputStream output = new FileOutputStream(partial)) {
                byte[] buffer = new byte[64 * 1024]; int count;
                while ((count = input.read(buffer)) != -1) {
                    received += count;
                    if (received > release.apkSize() || received > MAX_APK) throw new IOException("APK exceeds expected size");
                    output.write(buffer, 0, count); digest.update(buffer, 0, count);
                    if (progress != null) progress.onProgress(received, release.apkSize());
                    if (Thread.currentThread().isInterrupted()) throw new IOException("Download cancelled");
                }
            }
            if (received != release.apkSize()) throw new IOException("APK is incomplete");
            String actual = hex(digest.digest());
            if (!MessageDigest.isEqual(actual.getBytes(), release.sha256().getBytes()))
                throw new SecurityException("APK SHA-256 mismatch");
            ApkVerifier.verify(context, partial, release);
            if (!verifiedDir.mkdirs() && !verifiedDir.isDirectory()) throw new IOException("Verified storage unavailable");
            Files.move(partial.toPath(), verified.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING,
                java.nio.file.StandardCopyOption.ATOMIC_MOVE);
            preferences.setVerifiedManifest(preferences.availableManifest()); preferences.setError("");
            return verified;
        } catch (SecurityException exception) {
            Files.deleteIfExists(partial.toPath()); Files.deleteIfExists(verified.toPath());
            preferences.setVerifiedManifest(""); preferences.setError(exception.getMessage()); throw exception;
        } catch (IOException exception) {
            Files.deleteIfExists(partial.toPath()); preferences.setError(safeMessage(exception)); throw exception;
        }
    }

    public static void schedule(Context context) {
        UpdatePreferences preferences = new UpdatePreferences(context);
        WorkManager manager = WorkManager.getInstance(context);
        if (!preferences.automaticChecks()) { manager.cancelUniqueWork("jarvis-update-check"); return; }
        Constraints constraints = new Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build();
        PeriodicWorkRequest work = new PeriodicWorkRequest.Builder(UpdateCheckWorker.class, 8, TimeUnit.HOURS)
            .setConstraints(constraints).build();
        manager.enqueueUniquePeriodicWork("jarvis-update-check", ExistingPeriodicWorkPolicy.UPDATE, work);
    }

    public static UpdateRelease stored(String json) { return json.isBlank() ? null : UpdateRelease.parse(json); }
    private long currentVersionCode() throws IOException {
        try { return context.getPackageManager().getPackageInfo(context.getPackageName(), 0).getLongVersionCode(); }
        catch (android.content.pm.PackageManager.NameNotFoundException exception) { throw new IOException("Installed package version unavailable", exception); }
    }
    private static String hex(byte[] bytes) { StringBuilder out = new StringBuilder(); for (byte value : bytes) out.append(String.format("%02x", value)); return out.toString(); }
    private static String safeMessage(Exception exception) { return exception.getMessage() == null ? "Update request failed" : exception.getMessage(); }
    private static String httpError(int code) { return switch (code) { case 403 -> "Update server denied or rate-limited the request"; case 404 -> "Update feed is not published"; case 429 -> "Update server rate limit reached"; default -> code >= 500 ? "Update server is unavailable" : "Update server returned HTTP " + code; }; }
}
