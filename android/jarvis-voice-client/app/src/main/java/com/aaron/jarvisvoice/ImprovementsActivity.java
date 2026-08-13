package com.aaron.jarvisvoice;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Color;
import android.graphics.Insets;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageButton;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

public final class ImprovementsActivity extends Activity {
    private static final int BLACK = Color.rgb(20, 20, 20);
    private static final int MID = Color.rgb(103, 103, 103);
    private static final int LINE = Color.rgb(226, 226, 226);
    private static final int SOFT = Color.rgb(246, 246, 246);
    private static final int WHITE = Color.WHITE;

    private final Handler handler =
        new Handler(Looper.getMainLooper());

    private SecureStore store;
    private ImprovementApiClient api;

    private LinearLayout page;
    private LinearLayout candidateList;
    private TextView systemStatus;
    private TextView refreshStatus;

    private boolean loading = false;
    private boolean archiveMode = false;
    private Button activeTab;
    private Button archiveTab;

    private final Runnable autoRefresh =
        new Runnable() {
            @Override
            public void run() {
                loadCandidates(false);
                handler.postDelayed(this, 5000);
            }
        };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        store = new SecureStore(this);
        api = new ImprovementApiClient(store);

        configureWindow();
        setContentView(buildContent());
        applySystemBarAppearance();

        if (store.hasImprovementAdminToken()) {
            loadCandidates(true);
        } else {
            showMissingCredential();
        }
    }

    @Override
    protected void onStart() {
        super.onStart();
        AppVisibility.activityStarted();

        handler.removeCallbacks(autoRefresh);
        handler.postDelayed(autoRefresh, 5000);
    }

    @Override
    protected void onStop() {
        handler.removeCallbacks(autoRefresh);
        AppVisibility.activityStopped();
        super.onStop();
    }

    private void configureWindow() {
        Window window = getWindow();

        window.setStatusBarColor(Color.TRANSPARENT);
        window.setNavigationBarColor(Color.TRANSPARENT);
        window.setNavigationBarDividerColor(
            Color.TRANSPARENT
        );
    }

    private void applySystemBarAppearance() {
        View decor = getWindow().getDecorView();

        decor.post(() -> {
            WindowInsetsController controller =
                decor.getWindowInsetsController();

            if (controller == null) return;

            int appearance =
                WindowInsetsController
                    .APPEARANCE_LIGHT_STATUS_BARS
                | WindowInsetsController
                    .APPEARANCE_LIGHT_NAVIGATION_BARS;

            controller.setSystemBarsAppearance(
                appearance,
                appearance
            );
        });
    }

    private View buildContent() {
        page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(WHITE);

        page.addView(
            buildHeader(),
            matchWrap(0, dp(18))
        );

        systemStatus = text(
            "Self-improvement",
            14,
            BLACK
        );

        systemStatus.setTypeface(
            Typeface.create(
                "sans-serif-medium",
                Typeface.NORMAL
            )
        );

        page.addView(
            systemStatus,
            matchWrap(0, dp(8))
        );

        refreshStatus = text(
            "Connecting to Jarvis Core…",
            13,
            MID
        );

        page.addView(
            refreshStatus,
            matchWrap(0, dp(16))
        );

        page.addView(
            buildRequestCard(),
            matchWrap(0, dp(14))
        );

        page.addView(
            buildTabs(),
            matchWrap(0, dp(18))
        );

        candidateList = new LinearLayout(this);
        candidateList.setOrientation(
            LinearLayout.VERTICAL
        );

        page.addView(
            candidateList,
            matchWrap()
        );

        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setClipToPadding(false);

        scroll.addView(
            page,
            new ScrollView.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        );

        scroll.setOnApplyWindowInsetsListener(
            (view, insets) -> {
                Insets bars =
                    insets.getInsets(
                        WindowInsets.Type.systemBars()
                    );

                page.setPadding(
                    dp(16) + bars.left,
                    dp(8) + bars.top,
                    dp(16) + bars.right,
                    dp(30) + bars.bottom
                );

                return insets;
            }
        );

        scroll.requestApplyInsets();

        return scroll;
    }

    private View buildRequestCard() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(16), dp(16), dp(16), dp(16));
        card.setBackground(rounded(SOFT, 18, 1, LINE));

        TextView title = text(
            "Improve Jarvis",
            17,
            BLACK
        );
        title.setTypeface(
            Typeface.create(
                "sans-serif-medium",
                Typeface.NORMAL
            )
        );

        TextView note = text(
            "Request a change or capability. Jarvis will prepare it "
                + "through the existing supervised self-improvement system.",
            13,
            MID
        );
        note.setLineSpacing(0, 1.1f);
        note.setPadding(0, dp(5), 0, dp(14));

        Button request = primaryButton(
            "Request improvement"
        );
        request.setOnClickListener(
            view -> showRequestDialog()
        );

        card.addView(title, matchWrap());
        card.addView(note, matchWrap());
        card.addView(request, matchWrap());

        return card;
    }

    private View buildTabs() {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);

        activeTab = secondaryButton("Active");
        archiveTab = secondaryButton("Archive");

        activeTab.setOnClickListener(
            view -> setArchiveMode(false)
        );

        archiveTab.setOnClickListener(
            view -> setArchiveMode(true)
        );

        LinearLayout.LayoutParams left =
            new LinearLayout.LayoutParams(
                0,
                dp(42),
                1f
            );

        left.rightMargin = dp(5);

        LinearLayout.LayoutParams right =
            new LinearLayout.LayoutParams(
                0,
                dp(42),
                1f
            );

        right.leftMargin = dp(5);

        row.addView(activeTab, left);
        row.addView(archiveTab, right);

        updateTabs();

        return row;
    }

    private void setArchiveMode(boolean archived) {
        if (archiveMode == archived) return;

        archiveMode = archived;
        updateTabs();
        loadCandidates(true);
    }

    private void updateTabs() {
        if (activeTab == null || archiveTab == null) {
            return;
        }

        activeTab.setTextColor(
            archiveMode ? BLACK : WHITE
        );
        activeTab.setBackground(
            rounded(
                archiveMode ? SOFT : BLACK,
                14,
                1,
                archiveMode ? LINE : BLACK
            )
        );

        archiveTab.setTextColor(
            archiveMode ? WHITE : BLACK
        );
        archiveTab.setBackground(
            rounded(
                archiveMode ? BLACK : SOFT,
                14,
                1,
                archiveMode ? BLACK : LINE
            )
        );
    }

    private void showRequestDialog() {
        EditText input = new EditText(this);

        input.setHint(
            "What would you like Jarvis to improve?"
        );
        input.setMinLines(4);
        input.setMaxLines(8);
        input.setGravity(Gravity.TOP);
        input.setInputType(
            InputType.TYPE_CLASS_TEXT
                | InputType.TYPE_TEXT_FLAG_MULTI_LINE
                | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
        );

        LinearLayout wrapper =
            new LinearLayout(this);

        wrapper.setPadding(
            dp(20),
            dp(8),
            dp(20),
            0
        );

        wrapper.addView(
            input,
            matchWrap()
        );

        AlertDialog dialog =
            new AlertDialog.Builder(this)
                .setTitle("Request improvement")
                .setMessage(
                    "This creates a supervised improvement candidate. "
                        + "Nothing is deployed automatically."
                )
                .setView(wrapper)
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Request", null)
                .create();

        dialog.setOnShowListener(
            ignored -> dialog
                .getButton(
                    AlertDialog.BUTTON_POSITIVE
                )
                .setOnClickListener(view -> {
                    String request =
                        input.getText()
                            .toString()
                            .trim();

                    if (request.length() < 3) {
                        input.setError(
                            "Describe the improvement first."
                        );
                        return;
                    }

                    dialog
                        .getButton(
                            AlertDialog.BUTTON_POSITIVE
                        )
                        .setEnabled(false);

                    api.requestImprovement(
                        request,
                        new ImprovementApiClient
                            .JsonCallback() {
                            @Override
                            public void onSuccess(
                                JSONObject result
                            ) {
                                runOnUiThread(() -> {
                                    dialog.dismiss();
                                    archiveMode = false;
                                    updateTabs();

                                    Toast.makeText(
                                        ImprovementsActivity.this,
                                        "Improvement requested",
                                        Toast.LENGTH_SHORT
                                    ).show();

                                    loadCandidates(true);
                                });
                            }

                            @Override
                            public void onError(
                                String message
                            ) {
                                runOnUiThread(() -> {
                                    dialog
                                        .getButton(
                                            AlertDialog
                                                .BUTTON_POSITIVE
                                        )
                                        .setEnabled(true);

                                    Toast.makeText(
                                        ImprovementsActivity.this,
                                        message,
                                        Toast.LENGTH_LONG
                                    ).show();
                                });
                            }
                        }
                    );
                })
        );

        dialog.show();
    }

    private View buildHeader() {
        LinearLayout row = new LinearLayout(this);

        row.setOrientation(
            LinearLayout.HORIZONTAL
        );

        row.setGravity(
            Gravity.CENTER_VERTICAL
        );

        ImageButton back =
            new ImageButton(this);

        back.setImageResource(
            R.drawable.ic_back
        );

        back.setContentDescription("Back");
        back.setBackgroundColor(
            Color.TRANSPARENT
        );

        back.setOnClickListener(
            view -> finish()
        );

        row.addView(
            back,
            new LinearLayout.LayoutParams(
                dp(42),
                dp(42)
            )
        );

        LinearLayout titles =
            new LinearLayout(this);

        titles.setOrientation(
            LinearLayout.VERTICAL
        );

        titles.setPadding(
            dp(12),
            0,
            0,
            0
        );

        TextView title =
            text(
                "Improvements",
                25,
                BLACK
            );

        title.setTypeface(
            Typeface.create(
                "sans-serif-medium",
                Typeface.NORMAL
            )
        );

        titles.addView(
            title,
            matchWrap()
        );

        TextView subtitle =
            text(
                "Jarvis self-improvement",
                13,
                MID
            );

        subtitle.setPadding(
            0,
            dp(2),
            0,
            0
        );

        titles.addView(
            subtitle,
            matchWrap()
        );

        row.addView(
            titles,
            new LinearLayout.LayoutParams(
                0,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                1f
            )
        );

        Button refresh =
            secondaryButton("Refresh");

        refresh.setOnClickListener(
            view -> loadCandidates(true)
        );

        row.addView(
            refresh,
            new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                dp(42)
            )
        );

        return row;
    }

    private void loadCandidates(
        boolean visibleRefresh
    ) {
        if (loading) return;

        if (!store.hasImprovementAdminToken()) {
            showMissingCredential();
            return;
        }

        loading = true;

        if (visibleRefresh) {
            refreshStatus.setText(
                archiveMode
                    ? "Refreshing archive…"
                    : "Refreshing…"
            );
        }

        ImprovementApiClient.CandidatesCallback callback =
            new ImprovementApiClient.CandidatesCallback() {
                @Override
                public void onSuccess(
                    JSONArray items
                ) {
                    runOnUiThread(() -> {
                        loading = false;
                        renderCandidates(items);
                    });
                }

                @Override
                public void onError(
                    String message
                ) {
                    runOnUiThread(() -> {
                        loading = false;
                        showLoadError(message);
                    });
                }
            };

        if (archiveMode) {
            api.loadArchive(50, callback);
        } else {
            api.loadCandidates(50, callback);
        }
    }

    private void renderCandidates(
        JSONArray items
    ) {
        candidateList.removeAllViews();

        systemStatus.setText(
            archiveMode
                ? "Improvement archive"
                : "Self-improvement active"
        );

        refreshStatus.setText(
            archiveMode
                ? "Completed and dismissed improvements"
                : "Live · refreshes automatically"
        );

        if (items.length() == 0) {
            TextView empty =
                text(
                    archiveMode
                        ? "Archive is empty."
                        : "No active improvements.",
                    15,
                    MID
                );

            empty.setGravity(Gravity.CENTER);
            empty.setPadding(
                0,
                dp(60),
                0,
                dp(40)
            );

            candidateList.addView(
                empty,
                matchWrap()
            );

            return;
        }

        for (
            int index = 0;
            index < items.length();
            index++
        ) {
            JSONObject item =
                items.optJSONObject(index);

            if (item == null) continue;

            candidateList.addView(
                candidateCard(item),
                matchWrap(0, dp(12))
            );
        }
    }

    private View candidateCard(
        JSONObject item
    ) {
        LinearLayout card =
            new LinearLayout(this);

        card.setOrientation(
            LinearLayout.VERTICAL
        );

        card.setPadding(
            dp(16),
            dp(16),
            dp(16),
            dp(16)
        );

        card.setBackground(
            rounded(
                WHITE,
                18,
                1,
                LINE
            )
        );

        int candidateId =
            item.optInt(
                "candidate_id",
                0
            );

        int failureId =
            item.optInt(
                "failure_id",
                0
            );

        String status =
            clean(item, "status", "unknown");

        String summary =
            clean(item, "summary", "—");

        String risk =
            clean(item, "risk", "—");

        String model =
            clean(item, "model", "—");

        String updated =
            clean(item, "updated_at", "");

        TextView number =
            text(
                "Improvement #" + candidateId,
                13,
                MID
            );

        card.addView(
            number,
            matchWrap(0, dp(6))
        );

        TextView state =
            text(
                displayStatus(status),
                18,
                BLACK
            );

        state.setTypeface(
            Typeface.create(
                "sans-serif-medium",
                Typeface.NORMAL
            )
        );

        card.addView(
            state,
            matchWrap(0, dp(10))
        );

        TextView description =
            text(
                summary,
                15,
                BLACK
            );

        description.setLineSpacing(
            0,
            1.12f
        );

        card.addView(
            description,
            matchWrap(0, dp(14))
        );

        card.addView(
            detail(
                "Risk",
                capitalise(risk)
            ),
            matchWrap(0, dp(5))
        );

        card.addView(
            detail(
                "Model",
                model
            ),
            matchWrap(0, dp(5))
        );

        card.addView(
            detail(
                "Failure",
                failureId > 0
                    ? "#" + failureId
                    : "—"
            ),
            matchWrap(0, dp(5))
        );

        card.addView(
            detail(
                "Updated",
                shortTime(updated)
            ),
            matchWrap()
        );

        LinearLayout actions =
            new LinearLayout(this);

        actions.setOrientation(
            LinearLayout.HORIZONTAL
        );

        Button review =
            primaryButton("Review");

        review.setOnClickListener(
            view -> showCandidateReview(candidateId)
        );

        LinearLayout.LayoutParams reviewParams =
            new LinearLayout.LayoutParams(
                0,
                dp(42),
                1f
            );

        reviewParams.rightMargin = dp(5);

        actions.addView(
            review,
            reviewParams
        );

        if (archiveMode) {
            Button restore =
                secondaryButton("Restore");

            restore.setOnClickListener(
                view -> restoreCandidate(candidateId)
            );

            LinearLayout.LayoutParams restoreParams =
                new LinearLayout.LayoutParams(
                    0,
                    dp(42),
                    1f
                );

            restoreParams.leftMargin = dp(5);

            actions.addView(
                restore,
                restoreParams
            );
        } else if (isArchivable(status)) {
            Button archive =
                secondaryButton("Archive");

            archive.setOnClickListener(
                view -> archiveCandidate(candidateId)
            );

            LinearLayout.LayoutParams archiveParams =
                new LinearLayout.LayoutParams(
                    0,
                    dp(42),
                    1f
                );

            archiveParams.leftMargin = dp(5);

            actions.addView(
                archive,
                archiveParams
            );
        }

        card.addView(
            actions,
            matchWrap(dp(16), 0)
        );

        return card;
    }

    private void showCandidateReview(
        int candidateId
    ) {
        api.loadCandidate(
            candidateId,
            new ImprovementApiClient.JsonCallback() {
                @Override
                public void onSuccess(
                    JSONObject item
                ) {
                    runOnUiThread(() ->
                        showCandidateDialog(item)
                    );
                }

                @Override
                public void onError(
                    String message
                ) {
                    runOnUiThread(() ->
                        Toast.makeText(
                            ImprovementsActivity.this,
                            message,
                            Toast.LENGTH_LONG
                        ).show()
                    );
                }
            }
        );
    }

    private void showCandidateDialog(
        JSONObject item
    ) {
        int candidateId =
            item.optInt("candidate_id", 0);

        String status =
            clean(item, "status", "unknown");

        StringBuilder body =
            new StringBuilder();

        appendReview(
            body,
            "Status",
            displayStatus(status)
        );

        appendReview(
            body,
            "Summary",
            clean(item, "summary", "—")
        );

        appendReview(
            body,
            "Root cause",
            clean(item, "root_cause", "—")
        );

        appendReview(
            body,
            "Risk",
            capitalise(
                clean(item, "risk", "—")
            )
        );

        appendReview(
            body,
            "Model",
            clean(item, "model", "—")
        );

        appendReview(
            body,
            "Changed files",
            jsonValue(item, "changed_files")
        );

        appendReview(
            body,
            "Diff",
            jsonValue(item, "diff_stats")
        );

        appendReview(
            body,
            "Tests",
            jsonValue(item, "test_results")
        );

        appendReview(
            body,
            "Security",
            jsonValue(item, "security_results")
        );

        if (
            "candidate_ready".equals(status)
                || "awaiting_approval".equals(status)
        ) {
            appendReview(
                body,
                "Approval code",
                clean(
                    item,
                    "approval_code",
                    "—"
                )
            );
        }

        appendReview(
            body,
            "Error",
            clean(item, "error", "—")
        );

        appendReview(
            body,
            "Updated",
            shortTime(
                clean(item, "updated_at", "")
            )
        );

        AlertDialog.Builder builder =
            new AlertDialog.Builder(this)
                .setTitle(
                    "Improvement #" + candidateId
                )
                .setMessage(body.toString())
                .setNegativeButton(
                    "Close",
                    null
                );

        if (
            "candidate_ready".equals(status)
                || "awaiting_approval".equals(status)
        ) {
            builder.setPositiveButton(
                "Approve",
                (dialog, which) ->
                    showApprovalDialog(item)
            );

            builder.setNeutralButton(
                "Reject",
                (dialog, which) ->
                    confirmReject(candidateId)
            );
        } else if (
            "approved".equals(status)
        ) {
            builder.setPositiveButton(
                "Deploy",
                (dialog, which) ->
                    showManualDeployDialog(
                        candidateId
                    )
            );

            builder.setNeutralButton(
                "Reject",
                (dialog, which) ->
                    confirmReject(candidateId)
            );
        } else if (
            "deployed".equals(status)
        ) {
            builder.setPositiveButton(
                "Rollback",
                (dialog, which) ->
                    beginRollback(candidateId)
            );
        }

        builder.show();
    }

    private void showApprovalDialog(
        JSONObject item
    ) {
        int candidateId =
            item.optInt("candidate_id", 0);

        String approvalCode =
            clean(
                item,
                "approval_code",
                "—"
            );

        EditText input =
            codeInput("Approval code");

        LinearLayout wrapper =
            codeWrapper(input);

        AlertDialog dialog =
            new AlertDialog.Builder(this)
                .setTitle(
                    "Approve improvement #"
                        + candidateId
                )
                .setMessage(
                    "Approval code: "
                        + approvalCode
                        + "\n\nEnter the code to confirm approval. "
                        + "Approval does not deploy the change."
                )
                .setView(wrapper)
                .setNegativeButton(
                    "Cancel",
                    null
                )
                .setPositiveButton(
                    "Approve",
                    null
                )
                .create();

        dialog.setOnShowListener(
            ignored -> dialog
                .getButton(
                    AlertDialog.BUTTON_POSITIVE
                )
                .setOnClickListener(view -> {
                    String code =
                        input.getText()
                            .toString()
                            .trim();

                    if (code.length() < 6) {
                        input.setError(
                            "Enter the approval code."
                        );
                        return;
                    }

                    dialog
                        .getButton(
                            AlertDialog.BUTTON_POSITIVE
                        )
                        .setEnabled(false);

                    api.approve(
                        candidateId,
                        code,
                        new ImprovementApiClient
                            .JsonCallback() {
                            @Override
                            public void onSuccess(
                                JSONObject result
                            ) {
                                runOnUiThread(() -> {
                                    if (
                                        !commandSucceeded(
                                            result
                                        )
                                    ) {
                                        dialog
                                            .getButton(
                                                AlertDialog
                                                    .BUTTON_POSITIVE
                                            )
                                            .setEnabled(true);

                                        Toast.makeText(
                                            ImprovementsActivity.this,
                                            commandMessage(
                                                result,
                                                "Approval failed"
                                            ),
                                            Toast.LENGTH_LONG
                                        ).show();

                                        return;
                                    }

                                    dialog.dismiss();

                                    JSONObject details =
                                        result.optJSONObject(
                                            "details"
                                        );

                                    String deployCode =
                                        details == null
                                            ? ""
                                            : clean(
                                                details,
                                                "deploy_code",
                                                ""
                                            );

                                    if (
                                        deployCode.isBlank()
                                    ) {
                                        Toast.makeText(
                                            ImprovementsActivity.this,
                                            "Approved. No deployment code was returned.",
                                            Toast.LENGTH_LONG
                                        ).show();

                                        loadCandidates(true);
                                        return;
                                    }

                                    showDeployReadyDialog(
                                        candidateId,
                                        deployCode
                                    );

                                    loadCandidates(true);
                                });
                            }

                            @Override
                            public void onError(
                                String message
                            ) {
                                runOnUiThread(() -> {
                                    dialog
                                        .getButton(
                                            AlertDialog
                                                .BUTTON_POSITIVE
                                        )
                                        .setEnabled(true);

                                    Toast.makeText(
                                        ImprovementsActivity.this,
                                        message,
                                        Toast.LENGTH_LONG
                                    ).show();
                                });
                            }
                        }
                    );
                })
        );

        dialog.show();
    }

    private void showDeployReadyDialog(
        int candidateId,
        String deployCode
    ) {
        new AlertDialog.Builder(this)
            .setTitle(
                "Improvement approved"
            )
            .setMessage(
                "Deployment code\n\n"
                    + deployCode
                    + "\n\nThis is a separate one-time deployment code. "
                    + "It is not stored by the app."
            )
            .setNegativeButton(
                "Not now",
                null
            )
            .setPositiveButton(
                "Deploy now",
                (dialog, which) ->
                    deployCandidate(
                        candidateId,
                        deployCode
                    )
            )
            .show();
    }

    private void showManualDeployDialog(
        int candidateId
    ) {
        EditText input =
            codeInput("Deployment code");

        AlertDialog dialog =
            new AlertDialog.Builder(this)
                .setTitle(
                    "Deploy improvement #"
                        + candidateId
                )
                .setMessage(
                    "Enter the one-time deployment code generated when this improvement was approved."
                )
                .setView(
                    codeWrapper(input)
                )
                .setNegativeButton(
                    "Cancel",
                    null
                )
                .setPositiveButton(
                    "Deploy",
                    null
                )
                .create();

        dialog.setOnShowListener(
            ignored -> dialog
                .getButton(
                    AlertDialog.BUTTON_POSITIVE
                )
                .setOnClickListener(view -> {
                    String code =
                        input.getText()
                            .toString()
                            .trim();

                    if (code.length() < 6) {
                        input.setError(
                            "Enter the deployment code."
                        );
                        return;
                    }

                    dialog.dismiss();

                    deployCandidate(
                        candidateId,
                        code
                    );
                })
        );

        dialog.show();
    }

    private void deployCandidate(
        int candidateId,
        String deployCode
    ) {
        api.deploy(
            candidateId,
            deployCode,
            new ImprovementApiClient
                .JsonCallback() {
                @Override
                public void onSuccess(
                    JSONObject result
                ) {
                    runOnUiThread(() -> {
                        if (
                            !commandSucceeded(result)
                        ) {
                            Toast.makeText(
                                ImprovementsActivity.this,
                                commandMessage(
                                    result,
                                    "Deployment request failed"
                                ),
                                Toast.LENGTH_LONG
                            ).show();

                            return;
                        }

                        Toast.makeText(
                            ImprovementsActivity.this,
                            "Deployment requested",
                            Toast.LENGTH_SHORT
                        ).show();

                        loadCandidates(true);
                    });
                }

                @Override
                public void onError(
                    String message
                ) {
                    runOnUiThread(() ->
                        Toast.makeText(
                            ImprovementsActivity.this,
                            message,
                            Toast.LENGTH_LONG
                        ).show()
                    );
                }
            }
        );
    }

    private void confirmReject(
        int candidateId
    ) {
        new AlertDialog.Builder(this)
            .setTitle(
                "Reject improvement #"
                    + candidateId
            )
            .setMessage(
                "Reject this candidate? It will remain in the improvement history and can then be archived."
            )
            .setNegativeButton(
                "Cancel",
                null
            )
            .setPositiveButton(
                "Reject",
                (dialog, which) ->
                    rejectCandidate(candidateId)
            )
            .show();
    }

    private void rejectCandidate(
        int candidateId
    ) {
        api.reject(
            candidateId,
            new ImprovementApiClient
                .JsonCallback() {
                @Override
                public void onSuccess(
                    JSONObject result
                ) {
                    runOnUiThread(() -> {
                        if (
                            !commandSucceeded(result)
                        ) {
                            Toast.makeText(
                                ImprovementsActivity.this,
                                commandMessage(
                                    result,
                                    "Reject failed"
                                ),
                                Toast.LENGTH_LONG
                            ).show();

                            return;
                        }

                        Toast.makeText(
                            ImprovementsActivity.this,
                            "Improvement rejected",
                            Toast.LENGTH_SHORT
                        ).show();

                        loadCandidates(true);
                    });
                }

                @Override
                public void onError(
                    String message
                ) {
                    runOnUiThread(() ->
                        Toast.makeText(
                            ImprovementsActivity.this,
                            message,
                            Toast.LENGTH_LONG
                        ).show()
                    );
                }
            }
        );
    }

    private void beginRollback(
        int candidateId
    ) {
        api.issueRollbackTicket(
            candidateId,
            new ImprovementApiClient
                .JsonCallback() {
                @Override
                public void onSuccess(
                    JSONObject result
                ) {
                    runOnUiThread(() -> {
                        if (
                            !commandSucceeded(result)
                        ) {
                            Toast.makeText(
                                ImprovementsActivity.this,
                                commandMessage(
                                    result,
                                    "Could not issue rollback code"
                                ),
                                Toast.LENGTH_LONG
                            ).show();

                            return;
                        }

                        JSONObject details =
                            result.optJSONObject(
                                "details"
                            );

                        String rollbackCode =
                            details == null
                                ? ""
                                : clean(
                                    details,
                                    "rollback_code",
                                    ""
                                );

                        if (
                            rollbackCode.isBlank()
                        ) {
                            Toast.makeText(
                                ImprovementsActivity.this,
                                "No rollback code was returned.",
                                Toast.LENGTH_LONG
                            ).show();

                            return;
                        }

                        showRollbackDialog(
                            candidateId,
                            rollbackCode
                        );
                    });
                }

                @Override
                public void onError(
                    String message
                ) {
                    runOnUiThread(() ->
                        Toast.makeText(
                            ImprovementsActivity.this,
                            message,
                            Toast.LENGTH_LONG
                        ).show()
                    );
                }
            }
        );
    }

    private void showRollbackDialog(
        int candidateId,
        String rollbackCode
    ) {
        EditText input =
            codeInput("Rollback code");

        AlertDialog dialog =
            new AlertDialog.Builder(this)
                .setTitle(
                    "Rollback improvement #"
                        + candidateId
                )
                .setMessage(
                    "Rollback code: "
                        + rollbackCode
                        + "\n\nEnter the code below to confirm rollback. "
                        + "This is a separate one-time authorization."
                )
                .setView(
                    codeWrapper(input)
                )
                .setNegativeButton(
                    "Cancel",
                    null
                )
                .setPositiveButton(
                    "Rollback",
                    null
                )
                .create();

        dialog.setOnShowListener(
            ignored -> dialog
                .getButton(
                    AlertDialog.BUTTON_POSITIVE
                )
                .setOnClickListener(view -> {
                    String entered =
                        input.getText()
                            .toString()
                            .trim();

                    if (entered.length() < 6) {
                        input.setError(
                            "Enter the rollback code."
                        );
                        return;
                    }

                    dialog.dismiss();

                    rollbackCandidate(
                        candidateId,
                        entered
                    );
                })
        );

        dialog.show();
    }

    private void rollbackCandidate(
        int candidateId,
        String code
    ) {
        api.rollback(
            candidateId,
            code,
            new ImprovementApiClient
                .JsonCallback() {
                @Override
                public void onSuccess(
                    JSONObject result
                ) {
                    runOnUiThread(() -> {
                        if (
                            !commandSucceeded(result)
                        ) {
                            Toast.makeText(
                                ImprovementsActivity.this,
                                commandMessage(
                                    result,
                                    "Rollback request failed"
                                ),
                                Toast.LENGTH_LONG
                            ).show();

                            return;
                        }

                        Toast.makeText(
                            ImprovementsActivity.this,
                            "Rollback requested",
                            Toast.LENGTH_SHORT
                        ).show();

                        loadCandidates(true);
                    });
                }

                @Override
                public void onError(
                    String message
                ) {
                    runOnUiThread(() ->
                        Toast.makeText(
                            ImprovementsActivity.this,
                            message,
                            Toast.LENGTH_LONG
                        ).show()
                    );
                }
            }
        );
    }

    private EditText codeInput(
        String hint
    ) {
        EditText input =
            new EditText(this);

        input.setHint(hint);
        input.setSingleLine(true);

        input.setInputType(
            InputType.TYPE_CLASS_NUMBER
        );

        return input;
    }

    private LinearLayout codeWrapper(
        EditText input
    ) {
        LinearLayout wrapper =
            new LinearLayout(this);

        wrapper.setPadding(
            dp(20),
            dp(8),
            dp(20),
            0
        );

        wrapper.addView(
            input,
            matchWrap()
        );

        return wrapper;
    }

    private static boolean commandSucceeded(
        JSONObject result
    ) {
        return result.optBoolean(
            "success",
            false
        );
    }

    private static String commandMessage(
        JSONObject result,
        String fallback
    ) {
        return clean(
            result,
            "response",
            fallback
        );
    }

    private static void appendReview(
        StringBuilder body,
        String title,
        String value
    ) {
        if (body.length() > 0) {
            body.append("\n\n");
        }

        body.append(title)
            .append("\n")
            .append(value);
    }

    private static String jsonValue(
        JSONObject item,
        String key
    ) {
        if (!item.has(key) || item.isNull(key)) {
            return "—";
        }

        Object value = item.opt(key);

        if (value == null) {
            return "—";
        }

        String clean = String.valueOf(value).trim();

        return clean.isBlank()
                || "null".equalsIgnoreCase(clean)
            ? "—"
            : clean;
    }

    private void archiveCandidate(
        int candidateId
    ) {
        api.archive(
            candidateId,
            simpleActionCallback(
                "Archived"
            )
        );
    }

    private void restoreCandidate(
        int candidateId
    ) {
        api.restore(
            candidateId,
            simpleActionCallback(
                "Restored"
            )
        );
    }

    private ImprovementApiClient.JsonCallback
    simpleActionCallback(
        String successMessage
    ) {
        return new ImprovementApiClient
            .JsonCallback() {
            @Override
            public void onSuccess(
                JSONObject result
            ) {
                runOnUiThread(() -> {
                    Toast.makeText(
                        ImprovementsActivity.this,
                        successMessage,
                        Toast.LENGTH_SHORT
                    ).show();

                    loadCandidates(true);
                });
            }

            @Override
            public void onError(
                String message
            ) {
                runOnUiThread(() ->
                    Toast.makeText(
                        ImprovementsActivity.this,
                        message,
                        Toast.LENGTH_LONG
                    ).show()
                );
            }
        };
    }

    private static boolean isArchivable(
        String status
    ) {
        return "rejected".equals(status)
            || "failed".equals(status)
            || "rolled_back".equals(status)
            || "deployed".equals(status);
    }

    private static String clean(
        JSONObject item,
        String key,
        String fallback
    ) {
        if (!item.has(key) || item.isNull(key)) {
            return fallback;
        }

        String value =
            item.optString(key, "").trim();

        if (
            value.isBlank()
                || "null".equalsIgnoreCase(value)
        ) {
            return fallback;
        }

        return value;
    }

    private View detail(
        String name,
        String value
    ) {
        LinearLayout row =
            new LinearLayout(this);

        row.setOrientation(
            LinearLayout.HORIZONTAL
        );

        TextView label =
            text(
                name,
                13,
                MID
            );

        TextView data =
            text(
                value,
                13,
                BLACK
            );

        data.setGravity(
            Gravity.END
        );

        row.addView(
            label,
            new LinearLayout.LayoutParams(
                0,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                1f
            )
        );

        row.addView(
            data,
            new LinearLayout.LayoutParams(
                0,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                1f
            )
        );

        return row;
    }

    private void showMissingCredential() {
        candidateList.removeAllViews();

        systemStatus.setText(
            "Administrator access required"
        );

        refreshStatus.setText(
            "Connect this phone to the secured improvement API."
        );

        LinearLayout card =
            new LinearLayout(this);

        card.setOrientation(
            LinearLayout.VERTICAL
        );

        card.setPadding(
            dp(16),
            dp(16),
            dp(16),
            dp(16)
        );

        card.setBackground(
            rounded(
                SOFT,
                18,
                1,
                LINE
            )
        );

        TextView note =
            text(
                "Enter the Jarvis self-improvement administrator token. "
                    + "It will be stored encrypted in Android Keystore.",
                14,
                MID
            );

        card.addView(
            note,
            matchWrap(0, dp(14))
        );

        Button configure =
            secondaryButton(
                "Configure access"
            );

        configure.setOnClickListener(
            view -> showTokenDialog()
        );

        card.addView(
            configure,
            matchWrap()
        );

        candidateList.addView(
            card,
            matchWrap()
        );
    }

    private void showTokenDialog() {
        EditText input =
            new EditText(this);

        input.setSingleLine(true);

        input.setInputType(
            InputType.TYPE_CLASS_TEXT
                | InputType
                    .TYPE_TEXT_VARIATION_PASSWORD
        );

        input.setHint(
            "Improvement admin token"
        );

        int pad = dp(20);

        LinearLayout wrapper =
            new LinearLayout(this);

        wrapper.setPadding(
            pad,
            dp(8),
            pad,
            0
        );

        wrapper.addView(
            input,
            matchWrap()
        );

        new AlertDialog.Builder(this)
            .setTitle(
                "Improvement access"
            )
            .setMessage(
                "Paste the administrator token from your Jarvis Core."
            )
            .setView(wrapper)
            .setNegativeButton(
                "Cancel",
                null
            )
            .setPositiveButton(
                "Save",
                (dialog, which) -> {
                    try {
                        String value =
                            input.getText()
                                .toString()
                                .trim();

                        if (value.isBlank()) {
                            return;
                        }

                        store.saveImprovementAdminToken(
                            value
                        );

                        Toast.makeText(
                            this,
                            "Improvement access saved securely",
                            Toast.LENGTH_SHORT
                        ).show();

                        loadCandidates(true);
                    } catch (
                        Exception exception
                    ) {
                        Toast.makeText(
                            this,
                            "Could not save access",
                            Toast.LENGTH_LONG
                        ).show();
                    }
                }
            )
            .show();
    }

    private void showLoadError(
        String message
    ) {
        systemStatus.setText(
            "Could not load improvements"
        );

        refreshStatus.setText(message);

        candidateList.removeAllViews();

        Button retry =
            secondaryButton("Retry");

        retry.setOnClickListener(
            view -> loadCandidates(true)
        );

        candidateList.addView(
            retry,
            matchWrap(0, dp(10))
        );

        Button change =
            secondaryButton(
                "Change administrator access"
            );

        change.setOnClickListener(
            view -> showTokenDialog()
        );

        candidateList.addView(
            change,
            matchWrap()
        );
    }

    private static String displayStatus(
        String status
    ) {
        return switch (status) {
            case "queued" ->
                "Queued";
            case "generating" ->
                "Generating";
            case "candidate_ready",
                 "awaiting_approval" ->
                "Awaiting approval";
            case "approved" ->
                "Approved";
            case "deploy_requested" ->
                "Deployment requested";
            case "deploying" ->
                "Deploying";
            case "deployed" ->
                "Deployed";
            case "rollback_requested" ->
                "Rollback requested";
            case "rolling_back" ->
                "Rolling back";
            case "rolled_back" ->
                "Rolled back";
            case "rejected" ->
                "Rejected";
            case "failed" ->
                "Failed";
            default ->
                capitalise(
                    status.replace(
                        '_',
                        ' '
                    )
                );
        };
    }

    private static String capitalise(
        String value
    ) {
        if (
            value == null
                || value.isBlank()
        ) {
            return "Unknown";
        }

        return Character.toUpperCase(
            value.charAt(0)
        ) + value.substring(1);
    }

    private static String shortTime(
        String value
    ) {
        if (
            value == null
                || value.isBlank()
        ) {
            return "—";
        }

        String clean =
            value.replace(
                "T",
                " "
            );

        return clean.length() > 19
            ? clean.substring(0, 19)
            : clean;
    }

    private Button primaryButton(
        String label
    ) {
        Button button =
            new Button(this);

        button.setText(label);
        button.setTextColor(WHITE);
        button.setTextSize(14);
        button.setAllCaps(false);

        button.setTypeface(
            Typeface.create(
                "sans-serif-medium",
                Typeface.NORMAL
            )
        );

        button.setBackground(
            rounded(
                BLACK,
                14,
                1,
                BLACK
            )
        );

        return button;
    }

    private Button secondaryButton(
        String label
    ) {
        Button button =
            new Button(this);

        button.setText(label);
        button.setTextColor(BLACK);
        button.setTextSize(14);
        button.setAllCaps(false);

        button.setBackground(
            rounded(
                SOFT,
                14,
                1,
                LINE
            )
        );

        return button;
    }

    private TextView text(
        String value,
        int size,
        int colour
    ) {
        TextView view =
            new TextView(this);

        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(colour);

        return view;
    }

    private GradientDrawable rounded(
        int fill,
        int radius,
        int stroke,
        int strokeColour
    ) {
        GradientDrawable drawable =
            new GradientDrawable();

        drawable.setColor(fill);

        drawable.setCornerRadius(
            dp(radius)
        );

        drawable.setStroke(
            dp(stroke),
            strokeColour
        );

        return drawable;
    }

    private LinearLayout.LayoutParams matchWrap() {
        return matchWrap(0, 0);
    }

    private LinearLayout.LayoutParams matchWrap(
        int top,
        int bottom
    ) {
        LinearLayout.LayoutParams params =
            new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            );

        params.topMargin = top;
        params.bottomMargin = bottom;

        return params;
    }

    private int dp(int value) {
        return Math.round(
            value
                * getResources()
                    .getDisplayMetrics()
                    .density
        );
    }
}
