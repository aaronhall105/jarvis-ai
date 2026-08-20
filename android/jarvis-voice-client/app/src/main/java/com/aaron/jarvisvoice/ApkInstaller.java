package com.aaron.jarvisvoice;

import android.content.*;
import android.net.Uri;
import android.provider.Settings;
import androidx.core.content.FileProvider;
import java.io.File;

public final class ApkInstaller {
    private ApkInstaller() {}
    public static boolean hasPermission(Context context) { return context.getPackageManager().canRequestPackageInstalls(); }
    public static Intent permissionIntent(Context context) {
        return new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + context.getPackageName()));
    }
    public static void install(Context context, File apk) {
        Uri uri = FileProvider.getUriForFile(context, context.getPackageName() + ".updates", apk);
        Intent intent = new Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
        if (intent.resolveActivity(context.getPackageManager()) == null) throw new IllegalStateException("Android package installer is unavailable");
        context.startActivity(intent);
    }
}
