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
                "Refreshing…"
            );
        }

        api.loadCandidates(
            30,
            new ImprovementApiClient
                .CandidatesCallback() {
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
            }
        );
    }

    private void renderCandidates(
        JSONArray items
    ) {
        candidateList.removeAllViews();

        systemStatus.setText(
            "Self-improvement active"
        );

        refreshStatus.setText(
            "Live · refreshes automatically"
        );

        if (items.length() == 0) {
            TextView empty =
                text(
                    "No improvement candidates yet.",
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
            item.optString(
                "status",
                "unknown"
            );

        String summary =
            item.optString(
                "summary",
                "No summary available."
            );

        String risk =
            item.optString(
                "risk",
                "unknown"
            );

        String model =
            item.optString(
                "model",
                "unknown"
            );

        String updated =
            item.optString(
                "updated_at",
                ""
            );

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

        return card;
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
