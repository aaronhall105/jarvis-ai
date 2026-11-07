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
import java.util.HashSet;
import java.util.Iterator;
import java.util.Set;
import java.util.Arrays;
import java.util.Collections;
import java.util.regex.Pattern;
import org.json.JSONArray;
import org.json.JSONObject;

/** Device-held operator key. Public key enrollment is a separate host operation.
 * Neither Developer bearer authentication nor generating this key grants resume.
 */
final class AutonomyControl {
    private static final String ALIAS = "jarvis.autonomy.operator.v1";
    private static final String DEVELOPMENT_DOMAIN = "jarvis-development-admission-v1\n";
    private static final Set<String> DEVELOPMENT_FIELDS = new HashSet<>(java.util.Arrays.asList(
        "version", "action", "task_id", "source_sha", "parameters_sha256", "allowed_paths",
        "provider", "model", "max_calls", "max_cost_usd", "deadline_seconds",
        "guardian_generation"));
    private static final Pattern SHA40 = Pattern.compile("[a-f0-9]{40}");
    private static final Pattern SHA64 = Pattern.compile("[a-f0-9]{64}");
    private static final Set<String> PROTECTED_SCOPE_PREFIXES = Collections.unmodifiableSet(
        new HashSet<>(Arrays.asList(
            ".github/", "systemd/", "developer_gateway/",
            "tools/autonomy_", "tools/supervised_development_", "tools/deploy_",
            "tools/guardian", "tools/operator", "tools/auth", "tools/owner",
            "tools/patch", "tools/structured_edit")));
    private static final Set<String> EDITABLE_EXTENSIONS = Collections.unmodifiableSet(
        new HashSet<>(Arrays.asList(".py", ".pyi", ".toml", ".yaml", ".yml", ".json", ".md", ".sh")));
    private AutonomyControl() {}

    static JSONObject signed(String action, String generation) throws Exception {
        if (!action.equals("stop") && !action.equals("resume")) throw new IllegalArgumentException();
        UUID.fromString(generation);
        KeyStore store = operatorStore();
        String canonical = unsigned(action, generation, keyId(store),
            UUID.randomUUID().toString(), System.currentTimeMillis() / 1000 + 45);
        return sign(store, "jarvis-autonomy-control-v1\n", canonical);
    }

    /** Signs a broker-approved task envelope. The private key never leaves
     * Android Keystore; the display-only goal is bound by parameters_sha256. */
    static JSONObject signedDevelopment(JSONObject task) throws Exception {
        TreeMap<String, Object> fields = developmentFields(task);
        KeyStore store = operatorStore();
        fields.put("key_id", keyId(store));
        fields.put("request_id", task.getString("request_ref"));
        fields.put("expires_at", task.getLong("expires_at"));
        return sign(store, DEVELOPMENT_DOMAIN, canonical(fields));
    }

    static TreeMap<String, Object> developmentFields(JSONObject task) throws Exception {
        if (task == null) throw new IllegalArgumentException("missing task");
        Set<String> names = new HashSet<>();
        Iterator<String> iterator = task.keys();
        while (iterator.hasNext()) names.add(iterator.next());
        Set<String> taskFields = new HashSet<>(DEVELOPMENT_FIELDS);
        taskFields.add("request_ref"); taskFields.add("expires_at");
        Set<String> withGoal = new HashSet<>(taskFields); withGoal.add("goal");
        if (!names.equals(taskFields) && !names.equals(withGoal))
            throw new IllegalArgumentException("unexpected task fields");
        if (task.optInt("version", -1) != 1 || !"approve_development".equals(task.optString("action")))
            throw new IllegalArgumentException("invalid development action");
        UUID.fromString(task.getString("task_id"));
        UUID.fromString(task.getString("guardian_generation"));
        String requestRef = task.getString("request_ref");
        if (requestRef.isEmpty() || requestRef.length() > 200
            || !requestRef.matches("[A-Za-z0-9._:-]+")) throw new IllegalArgumentException("invalid request");
        long expires = task.getLong("expires_at");
        long now = System.currentTimeMillis() / 1000;
        if (expires <= now || expires > now + 120) throw new IllegalArgumentException("invalid expiry");
        if (!SHA40.matcher(task.getString("source_sha")).matches()
            || !SHA64.matcher(task.getString("parameters_sha256")).matches())
            throw new IllegalArgumentException("invalid source binding");
        if (!"openai".equals(task.getString("provider"))
            || !"gpt-5.3-codex".equals(task.getString("model"))
            || task.getInt("max_calls") != 1
            || task.getDouble("max_cost_usd") < 0 || task.getDouble("max_cost_usd") > 1
            || task.getInt("deadline_seconds") < 1 || task.getInt("deadline_seconds") > 120)
            throw new IllegalArgumentException("invalid development limits");
        String goal = task.optString("goal", "").trim();
        if (goal.isEmpty() || goal.length() > 4000) throw new IllegalArgumentException("invalid goal");
        JSONArray paths = task.getJSONArray("allowed_paths");
        if (paths.length() == 0 || paths.length() > 64) throw new IllegalArgumentException("invalid scope");
        String previous = null;
        for (int i = 0; i < paths.length(); i++) {
            String path = paths.getString(i);
            if (!isCanonicalEditablePath(path)
                || (previous != null && previous.compareTo(path) >= 0))
                throw new IllegalArgumentException("invalid scope");
            previous = path;
        }
        TreeMap<String, Object> fields = new TreeMap<>();
        for (String field : DEVELOPMENT_FIELDS) fields.put(field, task.get(field));
        return fields;
    }

    private static boolean isCanonicalEditablePath(String path) {
        if (path.isEmpty() || path.startsWith("/") || path.startsWith("./")
            || path.endsWith("/") || path.contains("\\") || path.contains("//")
            || path.indexOf('\0') >= 0 || !EDITABLE_EXTENSIONS.stream().anyMatch(path::endsWith))
            return false;
        for (String part : path.split("/", -1))
            if (part.isEmpty() || part.equals(".") || part.equals("..")) return false;
        for (String prefix : PROTECTED_SCOPE_PREFIXES)
            if (path.startsWith(prefix)) return false;
        return true;
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
