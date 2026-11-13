package com.aaron.jarvisvoice;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.graphics.Typeface;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import org.json.JSONArray;
import org.json.JSONObject;

/** Plain-language, evidence-backed development dashboard. Never an authority toggle. */
public final class AutonomyActivity extends Activity {
    private LinearLayout content;
    private AutonomyClient client;
    private JSONObject state;
    private boolean busy;
    private ImprovementApiClient improvements;
    private DevelopmentRequestStore requests;
    private JSONObject developmentRequest;
    private String requestError;
    private boolean requestLoading;
    private JSONObject productPreview;
    private String productKind = "";
    private String productIdentity = "";
    private String productError;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable refreshLoop = new Runnable() {
        @Override public void run() { refresh(); handler.postDelayed(this, 5000); }
    };

    @Override protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        client = new AutonomyClient(new SecureStore(this));
        improvements = new ImprovementApiClient(this);
        requests = new DevelopmentRequestStore(this);
        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(JarvisUi.WHITE);
        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        int padding = (int) (24 * getResources().getDisplayMetrics().density);
        content.setPadding(padding, padding * 2, padding, padding * 2);
        scroll.addView(content);
        setContentView(scroll);
    }
    @Override protected void onResume() {
        super.onResume(); refresh(); handler.postDelayed(refreshLoop, 5000);
    }
    @Override protected void onPause() { handler.removeCallbacks(refreshLoop); super.onPause(); }
    @Override protected void onDestroy() { handler.removeCallbacks(refreshLoop); client.close(); super.onDestroy(); }

    private void refresh() {
        if (busy) return;
        refreshRequest();
        busy = true;
        client.observe((value, error) -> {
            busy = false;
            if (error != null) { state = null; render(error); }
            else { state = value; render(null); }
        });
    }

    private void render(String error) {
        content.removeAllViews();
        button("Back", this::finish);
        heading("JARVIS DEVELOPMENT");
        renderRequest();
        button("Set up operator controls", this::enrollment);
        if (state == null) {
            heading("NEEDS ATTENTION"); text(error == null ? "Loading observations…" : error);
            button("Refresh", this::refresh); return;
        }
        String status = AutonomyPresentation.status(state);
        heading("AUTONOMOUS DEVELOPMENT " + status);
        text(state.optString("explanation"));
        if (!state.optString("observation_notice").isBlank()) text(state.optString("observation_notice"));
        heading("CURRENTLY WORKING ON");
        text(AutonomyPresentation.currentTitle(state));
        JSONObject current = state.optJSONObject("current");
        if (current == null) text("I'm watching for useful improvements.");
        else {
            heading("WHY"); text(current.optString("why"));
            heading("WHAT I'M DOING"); text(current.optString("doing"));
            text(AutonomyPresentation.progress(state));
        }
        JSONObject developmentTask = state.optJSONObject("development_task");
        heading("SUPERVISED DEVELOPMENT TASK");
        JSONObject lifecycle = state.optJSONObject("development_status");
        text(AutonomyPresentation.developmentStatus(lifecycle));
        if (lifecycle != null && !lifecycle.isNull("task_id")) {
            String taskId = lifecycle.optString("task_id");
            text("Task: " + taskId + "\nState: " + lifecycle.optString("lifecycle_state")
                + "\nProvider calls recorded: " + lifecycle.optInt("provider_calls"));
            if (lifecycle.optBoolean("approval_accepted") && "approval_requested".equals(lifecycle.optString("status")))
                button("Check separate activation prerequisites", () -> loadProduct("activation", taskId));
            if (lifecycle.optBoolean("proposal_available") && "review_required".equals(lifecycle.optString("status")))
                button("Review verified code proposal", () -> loadProduct("proposal", taskId));
        }
        if (developmentTask != null) renderDevelopmentTask(developmentTask,
            lifecycle != null && lifecycle.optBoolean("approval_accepted"));
        renderProductPreview();
        heading("WHAT I'M PLANNING");
        JSONArray plans = state.optJSONArray("plans");
        if (plans == null || plans.length() == 0) text("No evidence-backed plans yet.");
        else for (int i = 0; i < plans.length(); i++) {
            JSONObject plan = plans.optJSONObject(i);
            if (plan != null) text("Considering · " + plan.optString("title"));
        }
        heading("RECENT IMPROVEMENTS");
        renderList(state.optJSONArray("recent_improvements"), "No autonomous improvements installed yet.");
        heading("EXPERIMENTS AND ROLLBACKS");
        renderList(state.optJSONArray("rollbacks"), "No rollback outcomes recorded.");
        heading("WHAT JARVIS CAN DO");
        JSONArray capabilities = state.optJSONArray("capabilities");
        for (String category : new String[]{"HOME", "VOICE", "COMMUNICATION", "RESEARCH", "DEVELOPER", "MEMORY", "AUTOMATION"}) {
            if (capabilities == null) break;
            boolean shown = false;
            for (int i = 0; i < capabilities.length(); i++) {
                JSONObject item = capabilities.optJSONObject(i);
                if (item == null || !item.optString("category").equals(category)) continue;
                if (!shown) { heading(category); shown = true; }
                text(item.optString("name") + " · " + AutonomyPresentation.capability(item));
            }
        }
        text("Not yet verified means this dashboard has no current evidence. It does not mean the capability is broken.");
        heading("TODAY"); text(state.optString("daily_summary", "No confirmed development activity."));
        button("STOP AUTONOMOUS DEVELOPMENT", () -> confirm("stop"));
        if (status.equals("STOPPED") && !AutonomyPresentation.resumeAvailable(state))
            text("Global autonomous activation is unavailable. A verified task requires its separate task-bound confirmation above.");
        text("Operator key enrollment is required. A failed request never changes the displayed authority.");
        TextView details = text(state.optJSONObject("technical") == null ? "No details" : state.optJSONObject("technical").toString());
        details.setVisibility(View.GONE);
        button("Technical details", () -> details.setVisibility(details.getVisibility() == View.GONE ? View.VISIBLE : View.GONE));
        button("Refresh", this::refresh);
    }

    private void renderRequest() {
        heading("YOUR DEVELOPMENT REQUEST");
        if (requestError != null) text(requestError);
        if (developmentRequest != null) {
            if (!developmentRequest.optString("goal").isBlank()) text(developmentRequest.optString("goal"));
            text("Request: " + developmentRequest.optString("request_id")
                + "\nCandidate: " + developmentRequest.optInt("candidate_id")
                + "\nStatus: " + developmentRequest.optString("status")
                + "\n" + developmentRequest.optString("reason"));
            if (!developmentRequest.isNull("source_sha"))
                text("Source: " + developmentRequest.optString("source_sha")
                    + "\nFiles: " + developmentRequest.optJSONArray("affected_files"));
            if (developmentRequest.optJSONArray("acceptance_criteria") != null)
                text("Acceptance criteria: " + developmentRequest.optJSONArray("acceptance_criteria"));
            text("A recorded request does not authorize provider spending, activation, integration or deployment.");
            if ("ready".equals(developmentRequest.optString("status")))
                button("Review task preparation", () -> loadProduct("prepare", Integer.toString(developmentRequest.optInt("candidate_id"))));
            if ("needs_review".equals(developmentRequest.optString("status"))) {
                text("Jarvis still needs to inspect the source and propose an evidenced file scope. No task has started.");
                button("Ask Jarvis to inspect the requested improvement", () -> startActivity(
                    new android.content.Intent(this, MainActivity.class)
                        .putExtra(MainActivity.EXTRA_DEVELOPMENT_SCOPE_REQUEST, developmentRequest.optString("request_id"))));
            }
        }
        try {
            if (requests.pending() != null) {
                text("Submission confirmation is pending. Refresh reconciles the saved request identity.");
                button("Retry saved request", () -> submitRequest(null));
            } else button("Request a supervised improvement", this::requestImprovement);
        } catch (Exception invalid) { text("Saved request needs attention. Nothing will be resubmitted automatically."); }
    }

    private void requestImprovement() {
        EditText input = new EditText(this);
        input.setMinLines(3);
        input.setMaxLines(8);
        input.setHint("Describe the application improvement and the behavior you observed.");
        String objective = getIntent().getStringExtra("development_objective");
        if (objective != null) input.setText(objective);
        new AlertDialog.Builder(this).setTitle("Request supervised development")
            .setMessage("Jarvis records your objective for evidence and scope review. This does not start a task or authorize spending.")
            .setView(input).setNegativeButton("Cancel", null)
            .setPositiveButton("Record request", (dialog, which) -> submitRequest(input.getText().toString())).show();
    }

    private void submitRequest(String objective) {
        if (busy || requestLoading) return;
        try {
            JSONObject request = objective == null ? requests.pending() : requests.begin(objective);
            if (request == null) return;
            requestLoading = true;
            improvements.requestDevelopment(request, requestCallback());
        } catch (Exception invalid) {
            requestError = "Request could not be prepared. Reconcile any existing request and check its description.";
            render(null);
        }
    }

    private void refreshRequest() {
        String id = requests.lastRequestId();
        if (requestLoading || id.isBlank()) return;
        requestLoading = true;
        improvements.loadDevelopment(id, requestCallback());
    }

    private ImprovementApiClient.JsonCallback requestCallback() {
        return new ImprovementApiClient.JsonCallback() {
            @Override public void onSuccess(JSONObject result) {
                runOnUiThread(() -> {
                    if (isDestroyed()) return;
                    requestLoading = false;
                    try { requests.acknowledge(result); developmentRequest = result; requestError = null; }
                    catch (Exception invalid) { developmentRequest = null; requestError = "Request identity could not be verified."; }
                    if (!busy) render(null);
                });
            }
            @Override public void onError(String message) {
                runOnUiThread(() -> {
                    if (isDestroyed()) return;
                    requestLoading = false; requestError = message;
                    if (!busy) render(null);
                });
            }
        };
    }

    private void loadProduct(String kind, String identity) {
        if (busy) return;
        busy = true; productPreview = null; productError = null;
        productKind = kind; productIdentity = identity;
        AutonomyClient.Callback callback = (value, error) -> {
            busy = false; productPreview = value; productError = error; render(null);
        };
        if (kind.equals("prepare")) client.preparation(Integer.parseInt(identity), callback);
        else if (kind.equals("activation")) client.activation(identity, callback);
        else client.proposal(identity, callback);
    }

    private void renderProductPreview() {
        if (productError != null) { heading("PROTECTED REQUEST NEEDS ATTENTION"); text(productError); return; }
        if (productPreview == null) return;
        heading("PROTECTED " + productKind.toUpperCase(java.util.Locale.ROOT) + " REVIEW");
        if (productPreview.optInt("version") != 3 || !"ready".equals(productPreview.optString("status"))) {
            text("No authorization is available: " + productPreview.optString("reason", "protected service unavailable"));
            return;
        }
        JSONObject request = productPreview.optJSONObject("request");
        try {
            if (productKind.equals("prepare")) {
                AutonomyControl.preparationFields(request);
                if (developmentRequest == null || request.getInt("candidate_id") != developmentRequest.getInt("candidate_id")
                    || !request.getString("source_sha").equals(developmentRequest.getString("source_sha"))
                    || !request.getString("evidence_sha256").equals(developmentRequest.getString("evidence_sha256"))
                    || !request.getJSONArray("allowed_paths").toString().equals(developmentRequest.getJSONArray("affected_files").toString()))
                    throw new IllegalArgumentException("candidate changed");
                text(request.getString("goal") + "\nCandidate: " + request.getInt("candidate_id")
                    + "\nSource: " + request.getString("source_sha") + "\nFiles: " + request.getJSONArray("allowed_paths")
                    + "\nProvider/model: " + request.getString("provider") + "/" + request.getString("model")
                    + "\nMaximum calls/cost/deadline: " + request.getInt("max_calls") + " / $"
                    + request.get("max_cost_usd") + " / " + request.getInt("deadline_seconds") + " seconds"
                    + "\nEvidence: " + request.getString("evidence_sha256") + "\nExpires: " + request.getLong("expires_at"));
                button("Prepare this exact task", () -> confirmProduct(request));
            } else if (productKind.equals("activation")) {
                AutonomyControl.activationFields(request);
                if (!productIdentity.equals(request.getString("task_id"))) throw new IllegalArgumentException("task changed");
                text("The protected broker verified this task's separate owner approval."
                    + "\nTask: " + request.getString("task_id") + "\nApproval: " + request.getString("approval_id")
                    + "\nSource: " + request.getString("source_sha") + "\nParameters: " + request.getString("parameters_sha256")
                    + "\nGuardian generation: " + request.getString("guardian_generation")
                    + "\nExpires: " + request.getLong("expires_at"));
                button("Authorize separate task activation", () -> confirmProduct(request));
            } else {
                renderProposal(productPreview, request);
            }
        } catch (Exception invalid) {
            text("The protected preview is expired, changed or incomplete. Refresh it before continuing.");
        }
    }

    private void renderProposal(JSONObject preview, JSONObject request) throws Exception {
        // Validation includes the actual displayed diff; server claims alone never enable signing.
        JSONObject proposal = DevelopmentProposal.verified(preview, productIdentity);
        text("Verified proposal for task " + productIdentity + "\nBase: " + proposal.getString("base_sha")
            + "\nFiles: " + proposal.getJSONArray("allowed_paths") + "\nDiff SHA-256: " + proposal.getString("diff_sha256"));
        heading("TEST AND SECURITY EVIDENCE");
        text(proposal.getJSONArray("checks").toString(2));
        text("Provider calls: " + proposal.getInt("provider_calls") + "\nRecorded provider cost estimate: $" + proposal.get("cost_usd"));
        heading("PROPOSED SOURCE DIFF");
        TextView diff = text(proposal.getString("diff")); diff.setTextIsSelectable(true);
        diff.setTypeface(Typeface.MONOSPACE); diff.setTextSize(12);
        JSONObject handoff = preview.optJSONObject("handoff");
        if (handoff != null) {
            text("Recorded publication: " + handoff.toString(2));
            String url = DevelopmentProposal.verifiedPullRequestUrl(proposal, handoff);
            if (url != null) button("Open pull request", () -> startActivity(new android.content.Intent(
                android.content.Intent.ACTION_VIEW, android.net.Uri.parse(url))));
            else text("Publication is pending or needs reconciliation. Do not submit another approval; refresh to reconcile its recorded result.");
            return;
        }
        if (request == null) { text("PR publication is not configured. The proposal remains available for review."); return; }
        AutonomyControl.integrationFields(request);
        if (!DevelopmentProposal.matchesApproval(proposal, request)) throw new IllegalArgumentException("proposal changed");
        text("Separate approval publishes only this verified diff as a pull request. Merge and deployment remain separate.");
        button("Authorize pull request for this proposal", () -> confirmProduct(request));
    }

    private void confirmProduct(JSONObject request) {
        if (busy) return;
        String kind = productKind;
        String title = kind.equals("prepare") ? "Prepare this bounded task?"
            : kind.equals("activation") ? "Activate only this approved task?" : "Publish this reviewed proposal as a PR?";
        String explanation = kind.equals("prepare") ? "Your enrolled key signs this exact candidate and scope. The task still needs development approval and separate activation."
            : kind.equals("activation") ? "This separately authorizes Guardian activation for the approved task and bounded execution. The protected runtime must STOP Guardian afterward."
            : "This uses separate PR publication authority bound to the displayed diff and evidence. It does not authorize merging or deploying.";
        new AlertDialog.Builder(this).setTitle(title).setMessage(explanation)
            .setNegativeButton("Cancel", null).setPositiveButton("Confirm", (dialog, which) -> {
                busy = true;
                AutonomyClient.Callback callback = (result, error) -> {
                    busy = false; productPreview = null;
                    new AlertDialog.Builder(this).setMessage(error == null
                        ? "Protected request acknowledged. Refreshing the recorded result." : error)
                        .setPositiveButton("OK", null).show();
                    refresh();
                };
                if (kind.equals("prepare")) client.prepare(request, callback);
                else if (kind.equals("activation")) client.activate(request, callback);
                else client.publish(request, callback);
            }).show();
    }

    private void enrollment() {
        try {
            JSONObject request = AutonomyControl.enrollment();
            new AlertDialog.Builder(this).setTitle("Your phone's operator identity")
                .setMessage("Fingerprint:\n" + request.getString("key_id")
                    + "\n\nCompare this exact fingerprint during the local computer enrollment. "
                    + "Only the public request is copied. Your private key stays in Android Keystore. "
                    + "Copying this request does not enable controls or resume development.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Copy public request", (dialog, which) -> {
                    ClipboardManager clipboard = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
                    clipboard.setPrimaryClip(ClipData.newPlainText("Jarvis public operator enrollment", request.toString()));
                }).show();
        } catch (Exception unavailable) {
            new AlertDialog.Builder(this).setMessage("The phone's operator key is unavailable. Nothing was enrolled or enabled.")
                .setPositiveButton("OK", null).show();
        }
    }

    private void renderList(JSONArray items, String empty) {
        if (items == null || items.length() == 0) { text(empty); return; }
        for (int i = 0; i < items.length(); i++) {
            JSONObject item = items.optJSONObject(i);
            if (item != null) { text(item.optString("title")); text(item.optString("result")); }
        }
    }

    private void confirm(String action) {
        if (state == null || busy) return;
        boolean stop = action.equals("stop");
        new AlertDialog.Builder(this).setTitle(stop ? "Stop Jarvis improving himself?" : "Resume autonomous development?")
            .setMessage(stop ? "Jarvis will continue working normally, but automatic research, development and updates will stop."
                : "This requires your enrolled operator identity. Observe-only mode will not start engineering work.")
            .setNegativeButton("Cancel", null).setPositiveButton(stop ? "Stop" : "Resume", (dialog, which) -> {
                busy = true;
                client.control(action, state.optString("guardian_generation"), (result, error) -> {
                    busy = false;
                    if (error != null) new AlertDialog.Builder(this).setMessage(error).setPositiveButton("OK", null).show();
                    refresh(); // Never optimistically flip ACTIVE/STOPPED.
                });
            }).show();
    }

    private void renderDevelopmentTask(JSONObject task, boolean approvalAccepted) {
        long expires = task.optLong("expires_at", 0);
        if ("unavailable".equals(task.optString("status"))) {
            heading("OWNER REVIEW UNAVAILABLE");
            text("The protected task publisher is unavailable. Refresh later; no approval is possible.");
            return;
        }
        if (expires <= System.currentTimeMillis() / 1000) {
            heading("OWNER REVIEW EXPIRED");
            text("This development approval expired. Refresh for a new task; no approval is possible.");
            return;
        }
        if (!AutonomyPresentation.developmentApprovalAvailable(task)) {
            heading("OWNER REVIEW BLOCKED");
            text("The task envelope is incomplete or outside the signed approval policy. No approval is possible.");
            return;
        }
        heading("OWNER REVIEW REQUIRED");
        text(task.optString("goal", "A bounded development task is waiting for your review."));
        text("Task: " + task.optString("task_id") + "\nApproval: " + task.optString("request_ref")
            + "\nSource: " + task.optString("source_sha") + "\nParameters: "
            + task.optString("parameters_sha256") + "\nFiles: " + task.optJSONArray("allowed_paths")
            + "\nProvider/model: " + task.optString("provider") + "/" + task.optString("model")
            + "\nMaximum provider requests: " + task.optInt("max_calls")
            + "\nMaximum cost: $" + task.optString("max_cost_usd") + "\nDeadline: "
            + task.optString("deadline_seconds") + " seconds\nExpires: " + expires);
        if (approvalAccepted) text("The protected broker has verified this development approval. Activation remains separate.");
        else button("Authorize this development task", () -> confirmDevelopment(task));
    }

    private void confirmDevelopment(JSONObject task) {
        if (state == null || busy || !AutonomyPresentation.developmentApprovalAvailable(task)) return;
        new AlertDialog.Builder(this).setTitle("Authorize one development task?")
            .setMessage("Review the exact goal, source, files, model, cost and deadline above. "
                + "Your Android Keystore key will sign this task only; it will not activate Guardian.")
            .setNegativeButton("Cancel", null).setPositiveButton("Authorize", (dialog, which) -> {
                busy = true;
                client.approveDevelopment(task, (result, error) -> {
                    busy = false;
                    new AlertDialog.Builder(this)
                        .setMessage(error == null ? "Approval submitted. Guardian activation remains separate." : error)
                        .setPositiveButton("OK", null).show();
                    refresh();
                });
            }).show();
    }

    private TextView text(String value) {
        TextView view = new TextView(this); view.setText(value); view.setTextSize(16);
        view.setTextColor(JarvisUi.BLACK); view.setPadding(0, 12, 0, 12); content.addView(view); return view;
    }
    private void heading(String value) { TextView view = text(value); view.setTypeface(null, Typeface.BOLD); }
    private void button(String value, Runnable action) {
        Button view = new Button(this); view.setText(value); view.setTextColor(JarvisUi.BLACK);
        view.setAllCaps(false);
        view.setMinHeight((int) (JarvisUi.TOUCH_TARGET * getResources().getDisplayMetrics().density));
        view.setBackground(JarvisUi.rounded(this, JarvisUi.SOFT, JarvisUi.RADIUS_PILL, 1, JarvisUi.LINE));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.setMargins(0, 12, 0, 12);
        view.setOnClickListener(v -> action.run()); content.addView(view, params);
    }
}
