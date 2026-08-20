package com.aaron.jarvisvoice;

import android.content.Context;
import androidx.annotation.NonNull;
import androidx.work.*;
import java.io.IOException;

public final class UpdateCheckWorker extends Worker {
    public UpdateCheckWorker(@NonNull Context context, @NonNull WorkerParameters parameters) { super(context, parameters); }
    @NonNull @Override public Result doWork() {
        try {
            UpdateRelease release = new UpdateManager(getApplicationContext()).check();
            if (release == null) return Result.success();
            UpdateNotificationManager notifications = new UpdateNotificationManager(getApplicationContext());
            UpdatePreferences prefs = new UpdatePreferences(getApplicationContext());
            if (prefs.wifiAutoDownload()) {
                Constraints constraints = new Constraints.Builder().setRequiredNetworkType(NetworkType.UNMETERED).build();
                OneTimeWorkRequest download = new OneTimeWorkRequest.Builder(UpdateDownloadWorker.class).setConstraints(constraints).build();
                WorkManager.getInstance(getApplicationContext()).enqueueUniqueWork("jarvis-update-download", ExistingWorkPolicy.KEEP, download);
            } else notifications.available(release);
            return Result.success();
        } catch (IOException exception) { return getRunAttemptCount() < 3 ? Result.retry() : Result.failure(); }
    }
}
