package com.aaron.jarvisvoice;

import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.nio.charset.StandardCharsets;
import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.Signature;
import java.security.spec.ECGenParameterSpec;
import java.util.TreeMap;
import java.util.UUID;
import org.json.JSONObject;

/** Device-held operator key. Public key enrollment is a separate host operation.
 * Neither Developer bearer authentication nor generating this key grants resume.
 */
final class AutonomyControl {
    private static final String ALIAS = "jarvis.autonomy.operator.v1";
    private AutonomyControl() {}

    static JSONObject signed(String action, String generation) throws Exception {
        if (!action.equals("stop") && !action.equals("resume")) throw new IllegalArgumentException();
        UUID.fromString(generation);
        KeyStore store = operatorStore();
        String canonical = unsigned(action, generation, keyId(store),
            UUID.randomUUID().toString(), System.currentTimeMillis() / 1000 + 45);
        return sign(store, "jarvis-autonomy-control-v1\n", canonical);
    }

    /** Public SPKI and proof of possession only; never serializes the private key.
     * Exporting this request grants no authority. A local operator must enroll it.
     */
    static JSONObject enrollment() throws Exception {
        KeyStore store = operatorStore();
        String publicKey = Base64.encodeToString(store.getCertificate(ALIAS).getPublicKey().getEncoded(), Base64.NO_WRAP);
        TreeMap<String, Object> fields = new TreeMap<>();
        fields.put("key_id", keyId(store));
        fields.put("nonce", UUID.randomUUID().toString());
        fields.put("public_key", publicKey);
        fields.put("version", 1);
        return sign(store, "jarvis-autonomy-enrollment-v1\n", canonical(fields));
    }

    private static synchronized KeyStore operatorStore() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (!store.containsAlias(ALIAS)) {
            KeyPairGenerator generator = KeyPairGenerator.getInstance("EC", "AndroidKeyStore");
            generator.initialize(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
                .setAlgorithmParameterSpec(new ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256).build());
            generator.generateKeyPair();
        }
        return store;
    }

    private static String keyId(KeyStore store) throws Exception {
        byte[] publicKey = store.getCertificate(ALIAS).getPublicKey().getEncoded();
        StringBuilder fingerprint = new StringBuilder();
        for (byte b : MessageDigest.getInstance("SHA-256").digest(publicKey))
            fingerprint.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
        return fingerprint.toString();
    }

    private static JSONObject sign(KeyStore store, String domain, String canonical) throws Exception {
        Signature signer = Signature.getInstance("SHA256withECDSA");
        signer.initSign((java.security.PrivateKey) store.getKey(ALIAS, null));
        signer.update((domain + canonical).getBytes(StandardCharsets.UTF_8));
        JSONObject result = new JSONObject(canonical);
        result.put("signature", Base64.encodeToString(signer.sign(), Base64.NO_WRAP));
        return result;
    }

    static String unsigned(String action, String generation, String keyId, String requestId, long expires) {
        if (!action.equals("stop") && !action.equals("resume")) throw new IllegalArgumentException();
        UUID.fromString(generation);
        UUID.fromString(requestId);
        if (!keyId.matches("[a-f0-9]{64}") || expires <= 0) throw new IllegalArgumentException();
        TreeMap<String, Object> fields = new TreeMap<>();
        fields.put("action", action);
        fields.put("expires_at", expires);
        fields.put("generation", generation);
        fields.put("key_id", keyId);
        fields.put("request_id", requestId);
        fields.put("version", 1);
        return canonical(fields);
    }

    static String canonical(TreeMap<String, Object> fields) {
        StringBuilder canonical = new StringBuilder("{");
        for (java.util.Map.Entry<String, Object> entry : fields.entrySet()) {
            if (canonical.length() > 1) canonical.append(',');
            canonical.append(JSONObject.quote(entry.getKey())).append(':');
            canonical.append(entry.getValue() instanceof String
                ? JSONObject.quote((String) entry.getValue()) : entry.getValue().toString());
        }
        canonical.append('}');
        return canonical.toString();
    }
}
