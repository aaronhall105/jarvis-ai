package com.aaron.jarvisvoice;

import android.content.Context;
import android.content.pm.*;
import java.io.File;
import java.security.MessageDigest;
import java.security.cert.Certificate;
import java.util.*;

public final class ApkVerifier {
    private ApkVerifier() {}
    public static void verify(Context context, File apk, UpdateRelease release) {
        PackageManager manager = context.getPackageManager();
        PackageInfo archive = manager.getPackageArchiveInfo(apk.getAbsolutePath(), PackageManager.GET_SIGNING_CERTIFICATES);
        if (archive == null || archive.applicationInfo == null) fail("Downloaded file is not a valid APK");
        try {
            PackageInfo current = manager.getPackageInfo(context.getPackageName(), PackageManager.GET_SIGNING_CERTIFICATES);
            Set<String> trusted = certificates(current.signingInfo, true);
            Set<String> downloaded = certificates(archive.signingInfo, false);
            validateIdentity(context.getPackageName(), archive.packageName, release.versionName(), archive.versionName,
                release.versionCode(), archive.getLongVersionCode(), current.getLongVersionCode(), trusted, downloaded);
        } catch (PackageManager.NameNotFoundException exception) { fail("Installed Jarvis package cannot be verified"); }
    }
    static void validateIdentity(String expectedPackage, String actualPackage, String expectedName, String actualName,
            long expectedCode, long actualCode, long currentCode, Set<String> trusted, Set<String> downloaded) {
        if (!expectedPackage.equals(actualPackage)) fail("APK package name does not match Jarvis");
        if (!expectedName.equals(actualName)) fail("APK version name does not match manifest");
        if (actualCode != expectedCode) fail("APK version code does not match manifest");
        if (actualCode <= currentCode) fail("APK version code is not newer");
        if (trusted.isEmpty() || downloaded.isEmpty() || Collections.disjoint(trusted, downloaded))
            fail("APK signing certificate does not match installed Jarvis");
    }
    private static Set<String> certificates(SigningInfo info, boolean history) {
        Set<String> result = new HashSet<>();
        if (info == null) return result;
        android.content.pm.Signature[] signatures = history && !info.hasMultipleSigners()
            ? info.getSigningCertificateHistory() : info.getApkContentsSigners();
        try { MessageDigest digest = MessageDigest.getInstance("SHA-256");
            for (android.content.pm.Signature signature : signatures)
                result.add(Base64.getEncoder().encodeToString(digest.digest(signature.toByteArray())));
        } catch (Exception exception) { fail("Signing certificate verification failed"); }
        return result;
    }
    private static void fail(String message) { throw new SecurityException(message); }
}
