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
    static final String INTEGRATION_DOMAIN = "jarvis-development-integration-v1\n";
    static final String PREPARATION_DOMAIN = "jarvis-development-preparation-v1\n";
    static final String ACTIVATION_DOMAIN = "jarvis-development-activation-v1\n";
    private static final Set<String> INTEGRATION_FIELDS = Set.of(
        "version", "action", "request_id", "task_id", "repository", "base_branch",
        "base_sha", "base_tree_sha", "head_tree_sha", "allowed_paths", "parameters_sha256",
        "evidence_sha256", "diff_sha256", "proposal_sha256", "expires_at");
    private static final Set<String> PREPARATION_FIELDS = Set.of(
        "version", "action", "request_id", "candidate_id", "source_sha", "evidence_sha256",
        "candidate_sha256", "goal", "allowed_paths", "provider", "model", "max_calls",
        "max_cost_usd", "deadline_seconds", "guardian_generation", "expires_at");
    private static final Set<String> ACTIVATION_FIELDS = Set.of(
        "version", "action", "request_id", "task_id", "approval_id", "source_sha",
        "parameters_sha256", "guardian_generation", "expires_at");
    private static final Set<String> DEVELOPMENT_FIELDS = new HashSet<>(java.util.Arrays.asList(
        "version", "action", "task_id", "source_sha", "parameters_sha256", "allowed_paths",
        "provider", "model", "max_calls", "max_cost_usd", "deadline_seconds",
        "guardian_generation"));
    private static final Pattern SHA40 = Pattern.compile("[a-f0-9]{40}");
    private static final Pattern SHA64 = Pattern.compile("[a-f0-9]{64}");
    private static final Set<String> PROTECTED_SCOPE_PREFIXES = Collections.unmodifiableSet(
        new HashSet<>(Arrays.asList(
            ".", "data/", "logs/", "systemd/", "developer_gateway/", "updates/",
            "docs/JARVIS_PRODUCT_BASELINE.json", "docs/releases/",
            "tools/autonomy_", "tools/supervised_development_", "tools/self_improvement",
            "tools/self_development", "tools/deploy_", "tools/install_", "tools/enroll_",
            "tools/artifact_signing", "tools/publish_", "tools/generate_ota",
            "tools/verify_", "tools/check_", "tools/prepare_",
            "bridge/app/main.py", "bridge/app/config.py", "bridge/app/admin_engine.py",
            "bridge/app/self_improvement.py", "bridge/app/development_scope.py",
            "bridge/app/development_requests.py")));
    private static final Pattern PROTECTED_WORDS = Pattern.compile(
        "(?:^|[/_.-])(?:auth(?:entication|orization)?|security|secrets?|credentials?|passwords?|"
        + "tokens?|signing|keystore|guardian|broker|policy|policies|deploy(?:ment)?|sudoers|"
        + "permissions?|approvals?|patch_validation)(?:$|[/_.-])", Pattern.CASE_INSENSITIVE);
    private static final Set<String> EDITABLE_EXTENSIONS = Collections.unmodifiableSet(
        new HashSet<>(Arrays.asList(".py", ".pyi", ".toml", ".yaml", ".yml", ".json", ".md")));
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

    /** Separate consent for publishing the reviewed proposal. Never authorizes a merge or deployment. */
    static JSONObject signedIntegration(JSONObject request) throws Exception {
        TreeMap<String, Object> fields = integrationFields(request);
        KeyStore store = operatorStore();
        fields.put("key_id", keyId(store));
        return sign(store, INTEGRATION_DOMAIN, canonical(fields));
    }

    static JSONObject signedPreparation(JSONObject request) throws Exception {
        TreeMap<String, Object> fields = preparationFields(request);
        KeyStore store = operatorStore();
        fields.put("key_id", keyId(store));
        return sign(store, PREPARATION_DOMAIN, canonical(fields));
    }

    static JSONObject signedActivation(JSONObject request) throws Exception {
        TreeMap<String, Object> fields = activationFields(request);
        KeyStore store = operatorStore();
        fields.put("key_id", keyId(store));
        return sign(store, ACTIVATION_DOMAIN, canonical(fields));
    }

    static TreeMap<String, Object> preparationFields(JSONObject request) throws Exception {
        TreeMap<String, Object> fields = productFields(request, PREPARATION_FIELDS,
            "prepare_development", 300);
        Object candidate = request.get("candidate_id");
        if (!(candidate instanceof Number) || ((Number) candidate).doubleValue() != ((Number) candidate).longValue()
            || ((Number) candidate).longValue() < 1)
            throw new IllegalArgumentException("invalid candidate");
        requireSha(request, "source_sha", SHA40);
        requireSha(request, "evidence_sha256", SHA64);
        requireSha(request, "candidate_sha256", SHA64);
        requireUuid(request, "guardian_generation");
        validateDevelopmentPolicy(request);
        return fields;
    }

    static TreeMap<String, Object> activationFields(JSONObject request) throws Exception {
        TreeMap<String, Object> fields = productFields(request, ACTIVATION_FIELDS,
            "activate_development", 300);
        requireUuid(request, "task_id");
        requireUuid(request, "guardian_generation");
        String approval = request.getString("approval_id");
        if (!approval.matches("[A-Za-z0-9._:-]{1,200}")) throw new IllegalArgumentException("invalid approval");
        requireSha(request, "source_sha", SHA40);
        requireSha(request, "parameters_sha256", SHA64);
        return fields;
    }

    private static void requireUuid(JSONObject request, String field) throws Exception {
        String value = request.getString(field);
        if (!UUID.fromString(value).toString().equals(value)) throw new IllegalArgumentException("invalid identity");
    }

    private static void requireSha(JSONObject request, String field, Pattern pattern) throws Exception {
        if (!pattern.matcher(request.getString(field)).matches()) throw new IllegalArgumentException("invalid binding");
    }

    private static TreeMap<String, Object> productFields(JSONObject request, Set<String> expected,
            String action, int lifetime) throws Exception {
        if (request == null) throw new IllegalArgumentException("missing approval preview");
        Set<String> names = new HashSet<>();
        request.keys().forEachRemaining(names::add);
        Object version = request.get("version");
        Object expiry = request.get("expires_at");
        long now = System.currentTimeMillis() / 1000;
        if (!names.equals(expected) || !(version instanceof Integer) || ((Integer) version) != 1
            || !action.equals(request.getString("action")) || !(expiry instanceof Number)
            || ((Number) expiry).doubleValue() != ((Number) expiry).longValue()
            || request.getLong("expires_at") <= now || request.getLong("expires_at") > now + lifetime)
            throw new IllegalArgumentException("invalid or expired approval preview");
        requireUuid(request, "request_id");
        TreeMap<String, Object> fields = new TreeMap<>();
        for (String field : expected) fields.put(field, request.get(field));
        return fields;
    }

    static TreeMap<String, Object> integrationFields(JSONObject request) throws Exception {
        TreeMap<String, Object> fields = productFields(request, INTEGRATION_FIELDS,
            "create_development_pr", 900);
        if (!"aaronhall105/jarvis-ai".equals(request.getString("repository"))
            || !"jarvis/unified-production".equals(request.getString("base_branch")))
            throw new IllegalArgumentException("invalid proposal approval");
        for (String field : new String[]{"request_id", "task_id"}) {
            String id = request.getString(field);
            if (!UUID.fromString(id).toString().equals(id)) throw new IllegalArgumentException("invalid identity");
        }
        for (String field : new String[]{"base_sha", "base_tree_sha", "head_tree_sha"})
            if (!SHA40.matcher(request.getString(field)).matches()) throw new IllegalArgumentException("invalid source");
        for (String field : new String[]{"parameters_sha256", "evidence_sha256", "diff_sha256", "proposal_sha256"})
            if (!SHA64.matcher(request.getString(field)).matches()) throw new IllegalArgumentException("invalid evidence");
        long now = System.currentTimeMillis() / 1000;
        Object expires = request.get("expires_at");
        if (!(expires instanceof Number) || ((Number) expires).doubleValue() != ((Number) expires).longValue()
            || request.getLong("expires_at") <= now || request.getLong("expires_at") > now + 900)
            throw new IllegalArgumentException("expired proposal approval");
        JSONArray paths = request.getJSONArray("allowed_paths");
        String previous = null;
        if (paths.length() == 0) throw new IllegalArgumentException("empty proposal scope");
        for (int i = 0; i < paths.length(); i++) {
            String path = paths.getString(i);
            if (!isCanonicalEditablePath(path) || (previous != null && previous.compareTo(path) >= 0))
                throw new IllegalArgumentException("invalid proposal scope");
            previous = path;
        }
        return fields;
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
        validateDevelopmentPolicy(task);
        TreeMap<String, Object> fields = new TreeMap<>();
        for (String field : DEVELOPMENT_FIELDS) fields.put(field, task.get(field));
        return fields;
    }

    private static void validateDevelopmentPolicy(JSONObject task) throws Exception {
        Object rawCalls = task.get("max_calls");
        Object rawCost = task.get("max_cost_usd");
        Object rawDeadline = task.get("deadline_seconds");
        if (!(rawCalls instanceof Number) || !Double.isFinite(((Number) rawCalls).doubleValue())
            || ((Number) rawCalls).doubleValue() != ((Number) rawCalls).intValue()
            || !(rawCost instanceof Number) || !Double.isFinite(((Number) rawCost).doubleValue())
            || !(rawDeadline instanceof Number) || !Double.isFinite(((Number) rawDeadline).doubleValue())
            || ((Number) rawDeadline).doubleValue() != ((Number) rawDeadline).intValue())
            throw new IllegalArgumentException("invalid execution limit");
        if (!"openai".equals(task.getString("provider"))
            || !task.getString("model").matches("[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
            || task.getInt("max_calls") < 1 || task.getInt("max_calls") > 4
            || task.getDouble("max_cost_usd") < 0 || task.getDouble("max_cost_usd") > 1
            || task.getInt("deadline_seconds") < 1 || task.getInt("deadline_seconds") > 120)
            throw new IllegalArgumentException("invalid development limits");
        String goal = task.optString("goal", "").trim();
        if (goal.isEmpty() || goal.length() > 4000) throw new IllegalArgumentException("invalid goal");
        JSONArray paths = task.getJSONArray("allowed_paths");
        if (paths.length() == 0) throw new IllegalArgumentException("invalid scope");
        String previous = null;
        for (int i = 0; i < paths.length(); i++) {
            String path = paths.getString(i);
            if (!isCanonicalEditablePath(path)
                || (previous != null && previous.compareTo(path) >= 0))
                throw new IllegalArgumentException("invalid scope");
            previous = path;
        }
    }

    static boolean isCanonicalEditablePath(String path) {
        if (path == null || !path.matches("[A-Za-z0-9_./-]{1,240}")
            || PROTECTED_WORDS.matcher(path).find() || path.startsWith("/") || path.startsWith("./")
            || path.endsWith("/") || path.contains("\\") || path.contains("//")
            || path.indexOf('\0') >= 0 || !EDITABLE_EXTENSIONS.stream().anyMatch(path::endsWith))
            return false;
        String[] parts = path.split("/", -1);
        for (String part : parts)
            if (part.isEmpty() || part.startsWith(".")) return false;
        String basename = parts[parts.length - 1];
        if (basename.startsWith("compose.") || basename.startsWith("docker-compose.")
            || Arrays.asList("Dockerfile", "requirements.txt", "pyproject.toml", "conftest.py",
                "sitecustomize.py", "usercustomize.py", "pytest.ini", "setup.cfg", "ruff.toml").contains(basename))
            return false;
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
            canonical.append(canonicalString(entry.getKey())).append(':');
            canonical.append(canonicalValue(entry.getValue()));
        }
        canonical.append('}');
        return canonical.toString();
    }

    private static String canonicalValue(Object value) {
        if (value instanceof String) return canonicalString((String) value);
        if (value instanceof JSONArray) {
            JSONArray values = (JSONArray) value;
            StringBuilder result = new StringBuilder("[");
            for (int i = 0; i < values.length(); i++) {
                if (i > 0) result.append(',');
                try { result.append(canonicalValue(values.get(i))); }
                catch (org.json.JSONException invalid) { throw new IllegalArgumentException(invalid); }
            }
            return result.append(']').toString();
        }
        if (value instanceof Number) {
            // Sign the numeric value that JSONObject actually sends. In particular
            // an integral Double is transmitted as 1, not the signed text 1.0.
            try {
                String wire = JSONObject.numberToString((Number) value);
                if (!wire.contains(".") && !wire.contains("e") && !wire.contains("E"))
                    return new java.math.BigInteger(wire).toString();
                double number = Double.parseDouble(wire);
                if (!Double.isFinite(number)) throw new IllegalArgumentException("non-finite number");
                if (number == (long) number && Math.abs(number) < Long.MAX_VALUE)
                    return Long.toString((long) number);
                java.math.BigDecimal decimal = java.math.BigDecimal.valueOf(number).stripTrailingZeros();
                int exponent = decimal.precision() - decimal.scale() - 1;
                if (exponent < -4 || exponent >= 16) {
                    String coefficient = decimal.movePointLeft(exponent).toPlainString();
                    return coefficient + "e" + (exponent < 0 ? "-" : "+")
                        + String.format(java.util.Locale.ROOT, "%02d", Math.abs(exponent));
                }
                String plain = decimal.toPlainString();
                return plain.contains(".") ? plain : plain + ".0";
            } catch (org.json.JSONException invalid) { throw new IllegalArgumentException(invalid); }
        }
        throw new IllegalArgumentException("unsupported approval field");
    }

    private static String canonicalString(String value) {
        // Python broker json.dumps(ensure_ascii=True) does not escape '/'.
        // Android framework org.json does, unlike the JVM test dependency.
        StringBuilder result = new StringBuilder("\"");
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '"': result.append("\\\""); break;
                case '\\': result.append("\\\\"); break;
                case '\b': result.append("\\b"); break;
                case '\f': result.append("\\f"); break;
                case '\n': result.append("\\n"); break;
                case '\r': result.append("\\r"); break;
                case '\t': result.append("\\t"); break;
                default:
                    if (c < 0x20 || c > 0x7e) result.append(String.format(java.util.Locale.ROOT, "\\u%04x", (int) c));
                    else result.append(c);
            }
        }
        return result.append('"').toString();
    }
}
