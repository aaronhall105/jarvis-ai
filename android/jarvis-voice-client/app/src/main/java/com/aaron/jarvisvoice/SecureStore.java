package com.aaron.jarvisvoice;

import android.content.Context;
import android.content.SharedPreferences;
import android.provider.Settings;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.util.Locale;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

public final class SecureStore {
    private static final String PREFS = "jarvis_voice_settings";
    private static final String KEY_ALIAS = "jarvis_voice_token_v1710";
    private static final String TOKEN = "token";
    private final Context context;
    private final SharedPreferences preferences;

    public SecureStore(Context context) {
        this.context = context.getApplicationContext();
        this.preferences = this.context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    public void save(String baseUrl, String token, String pipeline, String wakePhrase) throws Exception {
        SharedPreferences.Editor editor = preferences.edit()
            .putString("base_url", trimTrailingSlash(baseUrl))
            .putString("pipeline", pipeline == null ? "" : pipeline.trim())
            .putString("wake_phrase", wakePhrase == null || wakePhrase.isBlank() ? "jarvis" : wakePhrase.trim());
        if (token != null && !token.isBlank() && !token.startsWith("••")) {
            editor.putString(TOKEN, encrypt(token.trim()));
        }
        editor.apply();
    }

    public String baseUrl() { return preferences.getString("base_url", ""); }
    public String pipeline() { return preferences.getString("pipeline", ""); }
    public String wakePhrase() { return preferences.getString("wake_phrase", "jarvis"); }
    public boolean hasToken() { return !preferences.getString(TOKEN, "").isBlank(); }

    public String token() {
        String encrypted = preferences.getString(TOKEN, "");
        if (encrypted.isBlank()) return "";
        try { return decrypt(encrypted); } catch (Exception ignored) { return ""; }
    }

    public String deviceId() {
        String androidId = Settings.Secure.getString(context.getContentResolver(), Settings.Secure.ANDROID_ID);
        if (androidId == null || androidId.isBlank()) androidId = "unknown";
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(androidId.getBytes(StandardCharsets.UTF_8));
            StringBuilder value = new StringBuilder("jarvis_android_");
            for (int i = 0; i < 8; i++) value.append(String.format(Locale.ROOT, "%02x", digest[i]));
            return value.toString();
        } catch (Exception ignored) {
            return "jarvis_android_" + Integer.toHexString(androidId.hashCode());
        }
    }

    private SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (store.containsAlias(KEY_ALIAS)) {
            return ((KeyStore.SecretKeyEntry) store.getEntry(KEY_ALIAS, null)).getSecretKey();
        }
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(
            KEY_ALIAS,
            KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT
        ).setBlockModes(KeyProperties.BLOCK_MODE_GCM)
         .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
         .build());
        return generator.generateKey();
    }

    private String encrypt(String value) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key());
        byte[] encrypted = cipher.doFinal(value.getBytes(StandardCharsets.UTF_8));
        return Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP) + ":" +
            Base64.encodeToString(encrypted, Base64.NO_WRAP);
    }

    private String decrypt(String value) throws Exception {
        String[] parts = value.split(":", 2);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP)));
        return new String(cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)), StandardCharsets.UTF_8);
    }

    private static String trimTrailingSlash(String value) {
        String result = value == null ? "" : value.trim();
        while (result.endsWith("/")) result = result.substring(0, result.length() - 1);
        return result;
    }
}
