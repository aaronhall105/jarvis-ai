package com.aaron.jarvisvoice;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Insets;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.ImageButton;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import java.util.List;
import java.util.Locale;

/** Integrations & accounts page inside the existing Jarvis Android app. */
public final class IntegrationsActivity extends Activity {
    private static final int BLACK = Color.rgb(20, 20, 20);
    private static final int MID = Color.rgb(102, 102, 102);
    private static final int LINE = Color.rgb(225, 225, 225);
    private static final int SOFT = Color.rgb(247, 247, 247);
    private static final int WHITE = Color.WHITE;

    private IntegrationsClient client;
    private LinearLayout providerList;
    private TextView coreState;
    private TextView emailAssistantState;
    private TextView emailAssistantDetails;
    private Switch importantEmailAlerts;
    private Switch replyAlerts;
    private Switch inboxCleanup;
    private Button importanceThreshold;
    private Button cleanupMode;
    private Button cleanupAge;
    private EmailAssistantSettings currentEmailSettings;
    private boolean renderingEmailSettings;
    private boolean oauthBrowserOpened;

    @Override protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        client = new IntegrationsClient(this);
        getWindow().setStatusBarColor(Color.TRANSPARENT);
        getWindow().setNavigationBarColor(Color.TRANSPARENT);
        setContentView(buildContent());
        handleReturn(getIntent());
    }

    @Override protected void onStart() {
        super.onStart();
        AppVisibility.activityStarted();
    }

    @Override protected void onStop() {
        AppVisibility.activityStopped();
        super.onStop();
    }

    @Override protected void onDestroy() {
        client.close();
        super.onDestroy();
    }

    @Override protected void onResume() {
        super.onResume();
        loadProviders();
        if (oauthBrowserOpened) {
            oauthBrowserOpened = false;
            coreState.setText("Checking whether account authorization completed…");
        }
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleReturn(intent);
    }

    private void handleReturn(Intent intent) {
        Uri data = intent == null ? null : intent.getData();
        if (
            data == null
                || !"jarvis".equals(data.getScheme())
                || !"integrations".equals(data.getHost())
                || !("/google".equals(data.getPath()) || "/microsoft".equals(data.getPath()))
        ) return;
        String provider = "/microsoft".equals(data.getPath()) ? "Outlook" : "Google";
        String status = data.getQueryParameter("status");
        if ("success".equals(status)) {
            Toast.makeText(
                this,
                provider + " returned to Jarvis; verifying provider health",
                Toast.LENGTH_LONG
            ).show();
        } else {
            Toast.makeText(
                this,
                provider + " authorization was cancelled or failed",
                Toast.LENGTH_LONG
            ).show();
        }
    }

    private View buildContent() {
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(dp(16), dp(8), dp(16), dp(30));
        page.setBackgroundColor(WHITE);
        page.addView(header(), matchWrap(0, dp(18)));

        coreState = text("Checking Jarvis Core…", 13, MID);
        coreState.setPadding(dp(12), dp(10), dp(12), dp(10));
        coreState.setBackground(rounded(SOFT, 14, 1, LINE));
        page.addView(coreState, matchWrap(0, dp(16)));

        TextView explanation = text(
            "Connected is shown only after Core verifies provider health. "
                + "Jarvis never receives your Google or Microsoft password, and tokens stay encrypted in Core.",
            13,
            MID
        );
        explanation.setLineSpacing(0f, 1.12f);
        page.addView(explanation, matchWrap(0, dp(18)));

        page.addView(buildEmailAssistantCard(), matchWrap(0, dp(18)));

        providerList = new LinearLayout(this);
        providerList.setOrientation(LinearLayout.VERTICAL);
        page.addView(providerList, matchWrap());

        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.addView(page, new ScrollView.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        scroll.setOnApplyWindowInsetsListener((view, insets) -> {
            Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
            page.setPadding(
                dp(16) + bars.left,
                dp(8) + bars.top,
                dp(16) + bars.right,
                dp(30) + bars.bottom
            );
            return insets;
        });
        return scroll;
    }

    private View header() {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        ImageButton back = new ImageButton(this);
        back.setImageResource(R.drawable.ic_back);
        back.setContentDescription("Back");
        back.setColorFilter(BLACK);
        back.setPadding(dp(10), dp(10), dp(10), dp(10));
        back.setBackground(rounded(SOFT, 21, 0, Color.TRANSPARENT));
        back.setOnClickListener(view -> finish());
        row.addView(back, new LinearLayout.LayoutParams(dp(42), dp(42)));

        LinearLayout copy = new LinearLayout(this);
        copy.setOrientation(LinearLayout.VERTICAL);
        copy.setPadding(dp(12), 0, 0, 0);
        TextView title = text("Integrations & accounts", 24, BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        copy.addView(title, matchWrap());
        copy.addView(text("Live provider access for Jarvis", 13, MID), matchWrap());
        row.addView(copy, new LinearLayout.LayoutParams(
            0,
            ViewGroup.LayoutParams.WRAP_CONTENT,
            1f
        ));
        return row;
    }

    private void loadProviders() {
        coreState.setText("Checking Jarvis Core and provider health…");
        client.providers(new IntegrationsClient.ProvidersCallback() {
            @Override public void onSuccess(List<IntegrationProvider> providers) {
                coreState.setText("Core online — provider states refreshed");
                renderProviders(providers);
            }

            @Override public void onError(IntegrationsClient.Failure failure) {
                showProviderFailure(failure);
            }
        });
        loadEmailAssistant();
    }

    private View buildEmailAssistantCard() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(16), dp(15), dp(16), dp(15));
        card.setBackground(rounded(SOFT, 18, 1, LINE));
        TextView title = text("Email Assistant", 18, BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        card.addView(title, matchWrap(0, dp(4)));
        emailAssistantState = text("Checking settings…", 12, MID);
        card.addView(emailAssistantState, matchWrap(0, dp(8)));

        importantEmailAlerts = settingSwitch("Important email alerts");
        replyAlerts = settingSwitch("Reply alerts");
        inboxCleanup = settingSwitch("Inbox cleanup");
        card.addView(importantEmailAlerts, matchWrap());
        card.addView(replyAlerts, matchWrap());
        card.addView(inboxCleanup, matchWrap(0, dp(8)));

        importanceThreshold = secondaryButton("Importance: Important");
        importanceThreshold.setOnClickListener(view -> cycleImportance());
        card.addView(importanceThreshold, matchWrap(0, dp(6)));
        cleanupMode = secondaryButton("Cleanup: Bin / Deleted Items");
        cleanupMode.setOnClickListener(view -> cycleCleanupMode());
        card.addView(cleanupMode, matchWrap(0, dp(6)));
        cleanupAge = secondaryButton("Cleanup age: 30 days");
        cleanupAge.setOnClickListener(view -> cycleCleanupAge());
        card.addView(cleanupAge, matchWrap(0, dp(8)));

        emailAssistantDetails = text(
            "Cleanup starts in preview mode and never permanently deletes email.",
            12,
            MID
        );
        emailAssistantDetails.setLineSpacing(0f, 1.1f);
        card.addView(emailAssistantDetails, matchWrap());

        importantEmailAlerts.setOnCheckedChangeListener((button, checked) -> saveEmailToggles());
        replyAlerts.setOnCheckedChangeListener((button, checked) -> saveEmailToggles());
        inboxCleanup.setOnCheckedChangeListener((button, checked) -> saveEmailToggles());
        return card;
    }

    private Switch settingSwitch(String label) {
        Switch value = new Switch(this);
        value.setText(label);
        value.setTextSize(14);
        value.setTextColor(BLACK);
        value.setMinHeight(dp(46));
        return value;
    }

    private void loadEmailAssistant() {
        client.emailAssistant(new IntegrationsClient.EmailAssistantCallback() {
            @Override public void onSuccess(EmailAssistantSettings settings) {
                renderEmailAssistant(settings);
            }

            @Override public void onError(IntegrationsClient.Failure failure) {
                emailAssistantState.setText("Settings unavailable — " + failure.message);
            }
        });
    }

    void renderEmailAssistant(EmailAssistantSettings settings) {
        currentEmailSettings = settings;
        renderingEmailSettings = true;
        importantEmailAlerts.setChecked(settings.importantAlerts);
        replyAlerts.setChecked(settings.replyAlerts);
        inboxCleanup.setChecked(settings.inboxCleanup);
        renderingEmailSettings = false;
        String state = settings.providerNeedsAttention
            ? "Needs reconnecting"
            : (settings.status.isBlank() ? "Paused" : titleCase(settings.status));
        if (settings.inboxCleanup && settings.cleanupDryRun) state += " · cleanup preview only";
        emailAssistantState.setText(state);
        importanceThreshold.setText("Importance: " + titleCase(settings.importanceThreshold));
        cleanupMode.setText(
            "Cleanup: " + (
                "trash".equals(settings.cleanupMode) ? "Bin / Deleted Items" : "Archive"
            )
        );
        cleanupAge.setText("Cleanup age: " + settings.cleanupAgeDays + " days");
        String lastCheck = settings.lastGmailCheck.isBlank() ? "Not checked yet" : settings.lastGmailCheck;
        String lastCleanup = settings.lastCleanup.isBlank() ? "Not run yet" : settings.lastCleanup;
        String nextCleanup = settings.nextCleanup.isBlank() ? "Not scheduled" : settings.nextCleanup;
        emailAssistantDetails.setText(
            "Last Gmail check: " + lastCheck
                + "\nLast cleanup: " + lastCleanup
                + "\nNext cleanup: " + nextCleanup
                + "\nProtected senders: " + settings.protectedSenders.size()
                + "\nCleanup never permanently deletes Gmail or Outlook mail."
        );
    }

    private void saveEmailToggles() {
        if (renderingEmailSettings || currentEmailSettings == null) return;
        saveEmailSettings(currentEmailSettings.withToggles(
            importantEmailAlerts.isChecked(),
            replyAlerts.isChecked(),
            inboxCleanup.isChecked()
        ));
    }

    private void cycleImportance() {
        if (currentEmailSettings == null) return;
        String next = switch (currentEmailSettings.importanceThreshold) {
            case "critical" -> "important";
            case "important" -> "normal";
            default -> "critical";
        };
        saveEmailSettings(currentEmailSettings.withOptions(
            next,
            currentEmailSettings.cleanupMode,
            currentEmailSettings.cleanupAgeDays
        ));
    }

    private void cycleCleanupMode() {
        if (currentEmailSettings == null) return;
        String next = "trash".equals(currentEmailSettings.cleanupMode) ? "archive" : "trash";
        saveEmailSettings(currentEmailSettings.withOptions(
            currentEmailSettings.importanceThreshold,
            next,
            currentEmailSettings.cleanupAgeDays
        ));
    }

    private void cycleCleanupAge() {
        if (currentEmailSettings == null) return;
        int next = currentEmailSettings.cleanupAgeDays == 30
            ? 60
            : (currentEmailSettings.cleanupAgeDays == 60 ? 90 : 30);
        saveEmailSettings(currentEmailSettings.withOptions(
            currentEmailSettings.importanceThreshold,
            currentEmailSettings.cleanupMode,
            next
        ));
    }

    private void saveEmailSettings(EmailAssistantSettings requested) {
        emailAssistantState.setText("Saving…");
        client.updateEmailAssistant(requested, new IntegrationsClient.EmailAssistantCallback() {
            @Override public void onSuccess(EmailAssistantSettings settings) {
                renderEmailAssistant(settings);
            }

            @Override public void onError(IntegrationsClient.Failure failure) {
                emailAssistantState.setText("Couldn't save settings — " + failure.message);
                renderEmailAssistant(currentEmailSettings);
            }
        });
    }

    private static String titleCase(String value) {
        if (value == null || value.isBlank()) return "Paused";
        return value.substring(0, 1).toUpperCase(Locale.ROOT) + value.substring(1);
    }

    void renderProviders(List<IntegrationProvider> providers) {
        providerList.removeAllViews();
        for (IntegrationProvider provider : providers) {
            providerList.addView(providerCard(provider), matchWrap(0, dp(12)));
        }
    }

    void showProviderFailure(IntegrationsClient.Failure failure) {
        String state;
        String detail;
        switch (failure.kind) {
            case AUTHENTICATION_REJECTED -> {
                coreState.setText("Core authentication rejected — check the mobile voice token in Settings");
                state = "Authentication required";
                detail = "Jarvis Core is reachable but rejected the saved mobile voice token";
            }
            case SETUP_REQUIRED -> {
                coreState.setText("Core online — integrations setup required");
                state = "Setup required";
                detail = failure.message;
            }
            case PROVIDER_UNAVAILABLE -> {
                coreState.setText("Core online — integrations provider unavailable");
                state = "Provider unavailable";
                detail = failure.message;
            }
            default -> {
                coreState.setText("Core unreachable — " + failure.message);
                state = "Core unreachable";
                detail = "Jarvis Core could not be reached to verify this provider";
            }
        }
        providerList.removeAllViews();
        for (String name : List.of(
            "Google", "Gmail", "Calendar", "Contacts", "Microsoft", "Web",
            "Home Assistant", "Instagram", "Facebook", "TikTok", "X"
        )) {
            IntegrationProvider provider = new IntegrationProvider(
                name.toLowerCase(Locale.ROOT).replace(" ", "_"),
                name,
                state,
                false,
                false,
                detail,
                List.of(),
                List.of(),
                "",
                "",
                false,
                false,
                false
            );
            providerList.addView(providerCard(provider), matchWrap(0, dp(12)));
        }
    }

    private View providerCard(IntegrationProvider provider) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(16), dp(15), dp(16), dp(15));
        card.setBackground(rounded(SOFT, 18, 1, LINE));

        LinearLayout heading = new LinearLayout(this);
        heading.setOrientation(LinearLayout.HORIZONTAL);
        heading.setGravity(Gravity.CENTER_VERTICAL);
        TextView name = text(provider.name, 16, BLACK);
        name.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        heading.addView(name, new LinearLayout.LayoutParams(
            0,
            ViewGroup.LayoutParams.WRAP_CONTENT,
            1f
        ));
        TextView state = text(provider.state, 12, provider.healthy ? WHITE : MID);
        state.setPadding(dp(10), dp(6), dp(10), dp(6));
        state.setBackground(rounded(
            provider.healthy ? BLACK : WHITE,
            12,
            1,
            provider.healthy ? BLACK : LINE
        ));
        heading.addView(state, wrapWrap());
        card.addView(heading, matchWrap(0, dp(8)));
        TextView detail = text(provider.detail(), 12, MID);
        detail.setLineSpacing(0f, 1.1f);
        card.addView(detail, matchWrap());

        if ("google".equals(provider.id)) addGoogleActions(card, provider);
        if ("microsoft".equals(provider.id)) addMicrosoftActions(card, provider);
        return card;
    }

    private void addGoogleActions(LinearLayout card, IntegrationProvider provider) {
        if (provider.canConnect || provider.canReconnect) {
            Button connect = button(provider.canReconnect ? "Reconnect Google" : "Connect Google");
            connect.setOnClickListener(view -> startGoogle());
            card.addView(connect, matchWrap(dp(12), 0));
        }
        if (provider.canDisconnect && !provider.accountId.isBlank()) {
            Button disconnect = secondaryButton("Disconnect Google");
            disconnect.setOnClickListener(view -> disconnectGoogle(provider.accountId));
            card.addView(disconnect, matchWrap(dp(8), 0));
        }
    }

    private void startGoogle() {
        coreState.setText("Starting Google authorization…");
        client.startGoogle(new IntegrationsClient.OAuthCallback() {
            @Override public void onSuccess(String authorizationUrl) {
                oauthBrowserOpened = true;
                startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(authorizationUrl)));
            }

            @Override public void onError(String message) {
                coreState.setText("Google setup failed — " + message);
            }
        });
    }

    private void disconnectGoogle(String accountId) {
        coreState.setText("Disconnecting Google…");
        client.disconnectGoogle(accountId, new IntegrationsClient.ResultCallback() {
            @Override public void onSuccess() {
                Toast.makeText(
                    IntegrationsActivity.this,
                    "Google disconnected from Jarvis",
                    Toast.LENGTH_LONG
                ).show();
                loadProviders();
            }

            @Override public void onError(String message) {
                coreState.setText("Google disconnect failed — " + message);
            }
        });
    }

    private void addMicrosoftActions(LinearLayout card, IntegrationProvider provider) {
        if (provider.canConnect || provider.canReconnect) {
            Button connect = button(provider.canReconnect ? "Reconnect Outlook" : "Connect Outlook");
            connect.setOnClickListener(view -> startMicrosoft());
            card.addView(connect, matchWrap(dp(12), 0));
        }
        if (provider.canDisconnect && !provider.accountId.isBlank()) {
            Button disconnect = secondaryButton("Disconnect Outlook");
            disconnect.setOnClickListener(view -> disconnectMicrosoft(provider.accountId));
            card.addView(disconnect, matchWrap(dp(8), 0));
        }
    }

    private void startMicrosoft() {
        coreState.setText("Starting Microsoft authorization…");
        client.startMicrosoft(new IntegrationsClient.OAuthCallback() {
            @Override public void onSuccess(String authorizationUrl) {
                oauthBrowserOpened = true;
                startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(authorizationUrl)));
            }

            @Override public void onError(String message) {
                coreState.setText("Outlook setup failed — " + message);
            }
        });
    }

    private void disconnectMicrosoft(String accountId) {
        coreState.setText("Disconnecting Outlook…");
        client.disconnectMicrosoft(accountId, new IntegrationsClient.ResultCallback() {
            @Override public void onSuccess() {
                Toast.makeText(
                    IntegrationsActivity.this,
                    "Outlook disconnected from Jarvis",
                    Toast.LENGTH_LONG
                ).show();
                loadProviders();
            }

            @Override public void onError(String message) {
                coreState.setText("Outlook disconnect failed — " + message);
            }
        });
    }

    private Button button(String label) {
        Button value = new Button(this);
        value.setText(label);
        value.setAllCaps(false);
        value.setTextColor(WHITE);
        value.setTextSize(14);
        value.setMinHeight(dp(48));
        value.setBackground(rounded(BLACK, 16, 0, Color.TRANSPARENT));
        return value;
    }

    private Button secondaryButton(String label) {
        Button value = button(label);
        value.setTextColor(BLACK);
        value.setBackground(rounded(WHITE, 16, 1, LINE));
        return value;
    }

    private TextView text(String value, int size, int colour) {
        TextView output = new TextView(this);
        output.setText(value);
        output.setTextSize(size);
        output.setTextColor(colour);
        return output;
    }

    private GradientDrawable rounded(int colour, int radius, int stroke, int strokeColour) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(colour);
        drawable.setCornerRadius(dp(radius));
        if (stroke > 0) drawable.setStroke(dp(stroke), strokeColour);
        return drawable;
    }

    private LinearLayout.LayoutParams matchWrap() {
        return matchWrap(0, 0);
    }

    private LinearLayout.LayoutParams matchWrap(int top, int bottom) {
        LinearLayout.LayoutParams value = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        );
        value.setMargins(0, top, 0, bottom);
        return value;
    }

    private LinearLayout.LayoutParams wrapWrap() {
        return new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        );
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
