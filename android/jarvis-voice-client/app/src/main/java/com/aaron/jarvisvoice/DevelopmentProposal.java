package com.aaron.jarvisvoice;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HashSet;
import java.util.Set;
import org.json.JSONArray;
import org.json.JSONObject;

/** Checks the exact display artifact before offering a separate PR signature. */
final class DevelopmentProposal {
    private DevelopmentProposal() { }

    static JSONObject verified(JSONObject preview, String taskId) throws Exception {
        if (preview == null || preview.optInt("version") != 3 || !"ready".equals(preview.optString("status")))
            throw new IllegalArgumentException("proposal unavailable");
        JSONObject proposal = preview.getJSONObject("proposal");
        DevelopmentRequestStore.requireId(taskId);
        if (!taskId.equals(proposal.getString("task_id"))
            || !"owner_review_required".equals(proposal.getString("risk")))
            throw new IllegalArgumentException("proposal identity mismatch");
        if (!proposal.getString("base_sha").matches("[a-f0-9]{40}"))
            throw new IllegalArgumentException("proposal source missing");
        for (String field : new String[]{"parameters_sha256", "evidence_sha256", "diff_sha256", "proposal_sha256"})
            if (!proposal.getString(field).matches("[a-f0-9]{64}"))
                throw new IllegalArgumentException("proposal binding missing");
        String diff = proposal.getString("diff");
        if (diff.isBlank() || diff.getBytes(StandardCharsets.UTF_8).length > 200_000
            || !sha256(diff).equals(proposal.getString("diff_sha256")))
            throw new IllegalArgumentException("displayed diff does not match evidence");
        Set<String> allowed = paths(proposal.getJSONArray("allowed_paths"));
        Set<String> changed = paths(proposal.getJSONArray("changed_files"));
        if (!allowed.containsAll(changed)) throw new IllegalArgumentException("proposal scope mismatch");
        JSONArray checks = proposal.getJSONArray("checks");
        if (checks.length() == 0) throw new IllegalArgumentException("missing verification checks");
        Set<String> names = new HashSet<>();
        for (int i = 0; i < checks.length(); i++) {
            JSONObject check = checks.getJSONObject(i);
            if (check.getInt("returncode") != 0 || !names.add(check.getString("name"))
                || check.getLong("duration_ms") < 0 || check.getJSONArray("command").length() == 0
                || !check.getString("output_sha256").matches("[a-f0-9]{64}"))
                throw new IllegalArgumentException("failed or unbound check");
            if ("pytest".equals(check.getString("name"))) {
                JSONObject counts = check.getJSONObject("test_counts");
                if (counts.getInt("executed") <= 0 || counts.getInt("failed") != 0
                    || counts.getInt("collected") < counts.getInt("executed"))
                    throw new IllegalArgumentException("no verified passing tests");
            }
        }
        if (!names.containsAll(Set.of("pytest", "ruff", "bandit", "compile")))
            throw new IllegalArgumentException("incomplete test or security checks");
        if (proposal.getInt("provider_calls") < 0 || !Double.isFinite(proposal.getDouble("cost_usd"))
            || proposal.getDouble("cost_usd") < 0) throw new IllegalArgumentException("invalid usage evidence");
        return proposal;
    }

    static boolean matchesApproval(JSONObject proposal, JSONObject approval) throws Exception {
        for (String field : new String[]{"task_id", "base_sha", "parameters_sha256", "evidence_sha256", "diff_sha256", "proposal_sha256"})
            if (!proposal.getString(field).equals(approval.getString(field))) return false;
        return paths(proposal.getJSONArray("allowed_paths")).equals(paths(approval.getJSONArray("allowed_paths")));
    }

    static String verifiedPullRequestUrl(JSONObject proposal, JSONObject handoff) {
        if (handoff == null || !"PR_REVIEW_REQUIRED".equals(handoff.optString("status"))) return null;
        try {
            Object number = handoff.get("number");
            if (!(number instanceof Number) || ((Number) number).doubleValue() != ((Number) number).longValue()
                || ((Number) number).longValue() <= 0
                || !proposal.getString("base_sha").equals(handoff.getString("base_sha"))
                || !proposal.getString("proposal_sha256").equals(handoff.getString("proposal_sha256"))
                || !handoff.getString("head_sha").matches("[a-f0-9]{40}")) return null;
            String expected = "https://github.com/aaronhall105/jarvis-ai/pull/" + ((Number) number).longValue();
            return expected.equals(handoff.getString("url")) ? expected : null;
        } catch (Exception invalid) { return null; }
    }

    private static Set<String> paths(JSONArray values) throws Exception {
        if (values.length() == 0) throw new IllegalArgumentException("empty scope");
        Set<String> result = new HashSet<>();
        String previous = null;
        for (int i = 0; i < values.length(); i++) {
            String path = values.getString(i);
            if (!AutonomyControl.isCanonicalEditablePath(path) || !result.add(path)
                || (previous != null && previous.compareTo(path) >= 0)) throw new IllegalArgumentException("invalid scope");
            previous = path;
        }
        return result;
    }

    static String sha256(String value) throws Exception {
        StringBuilder result = new StringBuilder();
        for (byte item : MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)))
            result.append(String.format(java.util.Locale.ROOT, "%02x", item & 255));
        return result.toString();
    }
}
