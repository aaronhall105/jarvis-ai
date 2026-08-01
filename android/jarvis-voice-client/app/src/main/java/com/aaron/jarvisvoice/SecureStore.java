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
import java.util.UUID;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

public final class SecureStore {
    private static final String PREFS = "jarvis_voice_settings";
    private static final String KEY_ALIAS = "jarvis_voice_token_v1710";
    private static final String MOBILE_TOKEN = "mobile_voice_token";
    private static final String HOME_ASSISTANT_TOKEN = "home_assistant_token_v1730";

    private final Context context;
    private final SharedPreferences preferences;

    public SecureStore(Context context) {
        this.context = context.getApplicationContext();
        this.preferences = this.context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        migrateAssistantDefaults();
        migrateWakeEngineDefaults();
        migrateWakeStabilityDefaults();
        migrateProductionVoiceDefaults();
        migrateReliableVoiceDefaults();
        migrateWakeReliabilityAlpha11();
        migrateVoiceOwnershipAlpha12();
    }

    private void migrateAssistantDefaults() {
        if (preferences.getBoolean("assistant_migration_v1810", false)) return;
        preferences.edit()
            .putBoolean("wake_enabled", true)
            .putBoolean("assistant_wake_always_v1810", true)
            .putBoolean("assistant_overlay_enabled_v1810", true)
            .putBoolean("assistant_start_voice_v1810", true)
            .putBoolean("background_conversations_v1800", true)
            .putBoolean("assistant_migration_v1810", true)
            .apply();
    }

    private void migrateWakeEngineDefaults() {
        if (preferences.getBoolean("wake_engine_migration_v1830", false)) return;
        preferences.edit()
            .putBoolean("dedicated_wake_enabled_v1830", true)
            .putFloat("wake_sensitivity_v1830", 0.65f)
            .putBoolean("wake_engine_migration_v1830", true)
            .apply();
    }

    private void migrateWakeStabilityDefaults() {
        if (
            preferences.getBoolean(
                "wake_stability_migration_v1840",
                false
            )
        ) {
            return;
        }

        float existing = preferences.getFloat(
            "wake_sensitivity_v1830",
            0.65f
        );

        preferences.edit()
            .putFloat(
                "wake_sensitivity_v1830",
                Math.max(existing, 0.78f)
            )
            .putBoolean(
                "wake_stability_migration_v1840",
                true
            )
            .apply();
    }

    private void migrateReliableVoiceDefaults() {
        if (preferences.getBoolean("voice_reliability_migration_v1841", false)) {
            return;
        }

        float sensitivity = preferences.getFloat(
            "wake_sensitivity_v1830",
            0.78f
        );

        preferences.edit()
            .putString(
                "conversation_mode_v1800",
                ConversationMode.STANDARD
            )
            .putBoolean("standard_auto_listen_v1800", true)
            .putBoolean("keep_conversation_open_v1800", true)
            .putBoolean("wake_enabled", true)
            .putBoolean("dedicated_wake_enabled_v1830", true)
            .putBoolean("assistant_wake_always_v1810", true)
            .putFloat(
                "wake_sensitivity_v1830",
                Math.max(sensitivity, 0.86f)
            )
            .putBoolean("voice_reliability_migration_v1841", true)
            .apply();
    }

    private void migrateProductionVoiceDefaults() {
        if (preferences.getBoolean("production_voice_migration_v1842", false)) {
            return;
        }

        float sensitivity = preferences.getFloat("wake_sensitivity_v1830", 0.78f);

        preferences.edit()
            .putString("conversation_mode_v1800", ConversationMode.STANDARD)
            .putBoolean("standard_auto_listen_v1800", true)
            .putBoolean("keep_conversation_open_v1800", true)
            .putBoolean("wake_enabled", true)
            .putString("wake_phrase", "jarvis")
            .putBoolean("dedicated_wake_enabled_v1830", true)
            .putBoolean("assistant_wake_always_v1810", true)
            .putBoolean("background_conversations_v1800", true)
            .putFloat("wake_sensitivity_v1830", Math.max(sensitivity, 0.90f))
            .putBoolean("production_voice_migration_v1842", true)
            .apply();
    }

    private void migrateWakeReliabilityAlpha11() {
        if (
            preferences.getBoolean(
                "wake_reliability_migration_v190110",
                false
            )
        ) {
            return;
        }

        float existing = preferences.getFloat(
            "wake_sensitivity_v1830",
            0.90f
        );

        preferences.edit()
            .putBoolean("wake_enabled", true)
            .putString("wake_phrase", "hey jarvis")
            .putBoolean("dedicated_wake_enabled_v1830", true)
            .putBoolean("assistant_wake_always_v1810", true)
            .putFloat(
                "wake_sensitivity_v1830",
                Math.min(existing, 0.72f)
            )
            .putBoolean(
                "wake_reliability_migration_v190110",
                true
            )
            .apply();
    }


    private void migrateVoiceOwnershipAlpha12() {
        if (
            preferences.getBoolean(
                "voice_ownership_migration_v190120",
                false
            )
        ) {
            return;
        }

        float existing = preferences.getFloat(
            "wake_sensitivity_v1830",
            0.90f
        );

        preferences.edit()
            .putBoolean("wake_enabled", true)
            .putString("wake_phrase", "jarvis")
            .putBoolean(
                "dedicated_wake_enabled_v1830",
                true
            )
            .putBoolean(
                "assistant_wake_always_v1810",
                true
            )
            .putBoolean(
                "standard_auto_listen_v1800",
                true
            )
            .putBoolean(
                "keep_conversation_open_v1800",
                true
            )
            .putFloat(
                "wake_sensitivity_v1830",
                Math.max(existing, 0.90f)
            )
            .putString(
                "voice_id",
                VoiceCatalog.ORIGINAL_ID
            )
            .putBoolean(
                "voice_ownership_migration_v190120",
                true
            )
            .apply();
    }

    public void resetToDedicatedWake() {
        float sensitivity = preferences.getFloat(
            "wake_sensitivity_v1830",
            0.90f
        );

        preferences.edit()
            .putBoolean("wake_enabled", true)
            .putString("wake_phrase", "jarvis")
            .putBoolean("dedicated_wake_enabled_v1830", true)
            .putBoolean("assistant_wake_always_v1810", true)
            .putFloat(
                "wake_sensitivity_v1830",
                Math.max(sensitivity, 0.90f)
            )
            .apply();
    }

    public void saveProduct(
        String coreUrl,
        String mobileToken,
        String userName,
        String conversationMode,
        String voiceId,
        String vadEagerness,
        boolean keepConversationOpen,
        boolean standardAutoListen,
        boolean wakeEnabled,
        String wakePhrase,
        boolean backgroundConversations,
        boolean startWithVoice,
        String homeAssistantUrl,
        String homeAssistantToken,
        String pipeline
    ) throws Exception {
        SharedPreferences.Editor editor = preferences.edit()
            .putString("core_url", trimTrailingSlash(coreUrl))
            .putString("realtime_user_name", normaliseUserName(userName))
            .putString("conversation_mode_v1800", ConversationMode.normalise(conversationMode))
            .putString("voice_id", VoiceCatalog.fromId(voiceId).id)
            .putString("vad_eagerness_v1800", normaliseEagerness(vadEagerness))
            .putBoolean("keep_conversation_open_v1800", keepConversationOpen)
            .putBoolean("standard_auto_listen_v1800", standardAutoListen)
            .putBoolean("wake_enabled", wakeEnabled)
            .putString("wake_phrase", normaliseWakePhrase(wakePhrase))
            .putBoolean("background_conversations_v1800", backgroundConversations)
            .putBoolean("start_with_voice_v1800", startWithVoice)
            .putString("home_assistant_url", trimTrailingSlash(homeAssistantUrl))
            .putString("home_assistant_pipeline", pipeline == null ? "" : pipeline.trim());
        if (mobileToken != null && !mobileToken.isBlank() && !mobileToken.startsWith("••")) {
            editor.putString(MOBILE_TOKEN, encrypt(mobileToken.trim()));
        }
        if (homeAssistantToken != null && !homeAssistantToken.isBlank() && !homeAssistantToken.startsWith("••")) {
            editor.putString(HOME_ASSISTANT_TOKEN, encrypt(homeAssistantToken.trim()));
        }
        editor.apply();
    }

    public void saveUnified(
        String coreUrl,
        String mobileToken,
        String userName,
        String voiceId,
        boolean wakeEnabled,
        String wakePhrase,
        String homeAssistantUrl,
        String homeAssistantToken,
        String pipeline
    ) throws Exception {
        saveProduct(
            coreUrl,
            mobileToken,
            userName,
            conversationMode(),
            voiceId,
            vadEagerness(),
            keepConversationOpen(),
            standardAutoListen(),
            wakeEnabled,
            wakePhrase,
            backgroundConversations(),
            startWithVoice(),
            homeAssistantUrl,
            homeAssistantToken,
            pipeline
        );
    }

    public String coreUrl() {
        return preferences.getString("core_url", "http://192.168.1.40:8000");
    }

    public String userName() {
        return preferences.getString("realtime_user_name", "Aaron");
    }

    public String userId() {
        String value = userName()
            .trim()
            .toLowerCase(Locale.ROOT)
            .replaceAll("[^a-z0-9]+", "_")
            .replaceAll("^_+|_+$", "");
        return value.isBlank() ? "user" : value;
    }

    public String conversationMode() {
        return ConversationMode.normalise(
            preferences.getString("conversation_mode_v1800", ConversationMode.STANDARD)
        );
    }

    public String voiceId() {
        return VoiceCatalog.fromId(
            preferences.getString(
                "voice_id",
                VoiceCatalog.ORIGINAL_ID
            )
        ).id;
    }

    public String vadEagerness() {
        return normaliseEagerness(preferences.getString("vad_eagerness_v1800", "high"));
    }

    public boolean keepConversationOpen() {
        return preferences.getBoolean("keep_conversation_open_v1800", true);
    }

    public boolean standardAutoListen() {
        return preferences.getBoolean("standard_auto_listen_v1800", true);
    }

    public boolean wakeEnabled() {
        return preferences.getBoolean("wake_enabled", false);
    }

    public String wakePhrase() {
        return normaliseWakePhrase(
            preferences.getString(
                "wake_phrase",
                "jarvis"
            )
        );
    }

    public boolean dedicatedWakeEnabled() {
        return preferences.getBoolean("dedicated_wake_enabled_v1830", true);
    }

    public float wakeSensitivity() {
        return clampSensitivity(
            preferences.getFloat(
                "wake_sensitivity_v1830",
                0.90f
            )
        );
    }

    public void setWakeWordOptions(
        boolean dedicated,
        float sensitivity
    ) {
        preferences.edit()
            .putBoolean("dedicated_wake_enabled_v1830", dedicated)
            .putFloat(
                "wake_sensitivity_v1830",
                clampSensitivity(sensitivity)
            )
            .apply();
    }

    public boolean backgroundConversations() {
        return preferences.getBoolean("background_conversations_v1800", true);
    }

    public boolean startWithVoice() {
        return preferences.getBoolean("start_with_voice_v1800", false);
    }

    public boolean assistantWakeAlways() {
        return preferences.getBoolean("assistant_wake_always_v1810", true);
    }

    public boolean assistantOverlayEnabled() {
        return preferences.getBoolean("assistant_overlay_enabled_v1810", true);
    }

    public boolean assistantStartsVoice() {
        return preferences.getBoolean("assistant_start_voice_v1810", true);
    }

    public void setAssistantOptions(
        boolean alwaysWake,
        boolean overlayEnabled,
        boolean startVoice
    ) {
        preferences.edit()
            .putBoolean("assistant_wake_always_v1810", alwaysWake)
            .putBoolean("assistant_overlay_enabled_v1810", overlayEnabled)
            .putBoolean("assistant_start_voice_v1810", startVoice)
            .apply();
    }

    public String homeAssistantUrl() {
        return preferences.getString("home_assistant_url", "");
    }

    public String homeAssistantPipeline() {
        return preferences.getString("home_assistant_pipeline", "");
    }

    public boolean hasMobileToken() {
        return !preferences.getString(MOBILE_TOKEN, "").isBlank();
    }

    public String mobileToken() {
        return decryptPreference(MOBILE_TOKEN);
    }

    public boolean hasHomeAssistantToken() {
        return !preferences.getString(HOME_ASSISTANT_TOKEN, "").isBlank();
    }

    public String homeAssistantToken() {
        return decryptPreference(HOME_ASSISTANT_TOKEN);
    }

    public String conversationId() {
        String existing = preferences.getString("conversation_id_v1800", "").trim();
        if (!existing.isEmpty()) return existing;
        String created = "mobile-chat-" + UUID.randomUUID();
        preferences.edit().putString("conversation_id_v1800", created).apply();
        return created;
    }

    public String newConversationId() {
        String created = "mobile-chat-" + UUID.randomUUID();
        preferences.edit().putString("conversation_id_v1800", created).apply();
        return created;
    }

    public void setConversationId(String value) {
        String candidate = value == null ? "" : value.trim();
        if (candidate.isEmpty() || candidate.length() > 200) return;
        preferences.edit()
            .putString("conversation_id_v1800", candidate)
            .apply();
    }

    public void setConversationMode(String value) {
        preferences.edit()
            .putString("conversation_mode_v1800", ConversationMode.normalise(value))
            .apply();
    }

    public String deviceId() {
        String androidId = Settings.Secure.getString(
            context.getContentResolver(),
            Settings.Secure.ANDROID_ID
        );
        if (androidId == null || androidId.isBlank()) androidId = "unknown";
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                .digest(androidId.getBytes(StandardCharsets.UTF_8));
            StringBuilder value = new StringBuilder("jarvis_android_");
            for (int i = 0; i < 8; i++) {
                value.append(String.format(Locale.ROOT, "%02x", digest[i]));
            }
            return value.toString();
        } catch (Exception ignored) {
            return "jarvis_android_" + Integer.toHexString(androidId.hashCode());
        }
    }

    private String decryptPreference(String key) {
        String encrypted = preferences.getString(key, "");
        if (encrypted.isBlank()) return "";
        try {
            return decrypt(encrypted);
        } catch (Exception ignored) {
            return "";
        }
    }

    private SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (store.containsAlias(KEY_ALIAS)) {
            return ((KeyStore.SecretKeyEntry) store.getEntry(KEY_ALIAS, null)).getSecretKey();
        }
        KeyGenerator generator = KeyGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_AES,
            "AndroidKeyStore"
        );
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
        return Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP)
            + ":"
            + Base64.encodeToString(encrypted, Base64.NO_WRAP);
    }

    private String decrypt(String value) throws Exception {
        String[] parts = value.split(":", 2);
        if (parts.length != 2) throw new IllegalArgumentException("Invalid encrypted token");
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(
            Cipher.DECRYPT_MODE,
            key(),
            new GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP))
        );
        return new String(
            cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)),
            StandardCharsets.UTF_8
        );
    }

    private static String trimTrailingSlash(String value) {
        String result = value == null ? "" : value.trim();
        while (result.endsWith("/")) result = result.substring(0, result.length() - 1);
        return result;
    }

    private static String normaliseUserName(String value) {
        String result = value == null ? "" : value.trim();
        return result.isBlank() ? "Aaron" : result;
    }

    private static String normaliseWakePhrase(String value) {
        String result = WakePhrasePolicy.normalise(value);
        return result.isBlank() ? "jarvis" : result;
    }

    private static String normaliseEagerness(String value) {
        String result = value == null ? "" : value.trim().toLowerCase(Locale.ROOT);
        return switch (result) {
            case "low", "medium", "high" -> result;
            default -> "high";
        };
    }

    private static float clampSensitivity(float value) {
        return Math.max(0.1f, Math.min(1.0f, value));
    }
}
