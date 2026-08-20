package com.aaron.jarvisvoice;

import android.content.Context;
import androidx.annotation.NonNull;
import androidx.work.*;

public final class UpdateDownloadWorker extends Worker {
    public UpdateDownloadWorker(@NonNull Context context, @NonNull WorkerParameters parameters) { super(context, parameters); }
    @NonNull @Override public Result doWork() {
        UpdatePreferences prefs = new UpdatePreferences(getApplicationContext());
        try {
            UpdateRelease release = UpdateManager.stored(prefs.availableManifest());
            if (release == null) return Result.failure();
            new UpdateManager(getApplicationContext()).download(release, null);
            new UpdateNotificationManager(getApplicationContext()).ready(release);
            return Result.success();
        } catch (SecurityException exception) {
            new UpdateNotificationManager(getApplicationContext()).failed(exception.getMessage()); return Result.failure();
        } catch (Exception exception) {
            if (getRunAttemptCount() < 3) return Result.retry();
            new UpdateNotificationManager(getApplicationContext()).failed(exception.getMessage() == null ? "Download failed" : exception.getMessage());
            return Result.failure();
        }
    }
}
