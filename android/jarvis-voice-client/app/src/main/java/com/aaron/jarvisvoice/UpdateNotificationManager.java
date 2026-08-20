package com.aaron.jarvisvoice;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;

public final class UpdateNotificationManager {
    public static final String CHANNEL_ID = "jarvis_updates";
    private final Context context;
    private final NotificationManager manager;
    public UpdateNotificationManager(Context context) {
        this.context = context; manager = context.getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel(CHANNEL_ID, "Jarvis updates", NotificationManager.IMPORTANCE_DEFAULT));
    }
    public void available(UpdateRelease release) { notifyOnce(release, 19015, "Jarvis update available", release.versionName() + " is ready to download"); }
    public void ready(UpdateRelease release) { notifyOnce(release, 19016, "Jarvis update ready", release.versionName() + " is verified and ready to install"); }
    public void failed(String message) { notify(19017, "Jarvis update failed", message); }
    private void notifyOnce(UpdateRelease release, int id, String title, String text) {
        android.content.SharedPreferences prefs = context.getSharedPreferences("jarvis_update_notifications", Context.MODE_PRIVATE);
        String key = id + ":" + release.versionName(); if (prefs.getBoolean(key, false)) return;
        notify(id, title, text); prefs.edit().putBoolean(key, true).apply();
    }
    private void notify(int id, String title, String text) {
        if (android.os.Build.VERSION.SDK_INT >= 33 && context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return;
        PendingIntent open = PendingIntent.getActivity(context, id, new Intent(context, UpdatesActivity.class), PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification notification = new Notification.Builder(context, CHANNEL_ID).setSmallIcon(R.drawable.ic_jarvis_status)
            .setContentTitle(title).setContentText(text).setAutoCancel(true).setContentIntent(open).build();
        manager.notify(id, notification);
    }
}
