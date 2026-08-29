package com.aaron.jarvisvoice;

import android.app.Activity;
import android.app.role.RoleManager;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Insets;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageButton;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import java.util.List;

public final class SettingsActivity extends Activity {
    static final String INTEGRATIONS_SECTION_TAG = "settings_integrations_section";
    static final String INTEGRATIONS_CONTENT_TAG = "settings_integrations_content";
    static final String INTEGRATIONS_BUTTON_TAG = "settings_integrations_open";

    private static final int BLACK = Color.rgb(20, 20, 20);
    private static final int MID = Color.rgb(102, 102, 102);
    private static final int LINE = Color.rgb(225, 225, 225);
    private static final int SOFT = Color.rgb(247, 247, 247);
    private static final int WHITE = Color.WHITE;
    private static final int REQUEST_ASSISTANT_ROLE = 1810;
    private static final int REQUEST_WAKE_PERMISSION = 1811;

    private SecureStore store;

    private TextView assistantStatus;
    private TextView wakeEngineStatus;
    private TextView voiceFoundationStatus;

    private Spinner conversationMode;
    private Spinner voice;
    private Spinner responsiveness;
    private Spinner wakeSensitivity;

    private Switch keepOpen;
    private Switch standardAutoListen;
    private Switch wakeEnabled;
    private Switch dedicatedWake;
    private Switch backgroundConversations;
    private Switch startWithVoice;
    private Switch assistantWakeAlways;
    private Switch assistantOverlay;
    private Switch assistantStartVoice;

    private EditText wakePhrase;
    private EditText coreUrl;
    private EditText remoteCoreUrl;
    private EditText mobileToken;
    private EditText userName;
    private EditText homeAssistantUrl;
    private EditText homeAssistantToken;
    private EditText pipeline;

    @Override protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        store = new SecureStore(this);
        configureWindow();
        setContentView(buildContent());
        applySystemBarAppearance();
        loadSettings();
    }

    @Override protected void onStart() {
        super.onStart();
        AppVisibility.activityStarted();
    }

    @Override protected void onStop() {
        AppVisibility.activityStopped();
        super.onStop();
    }

    private void configureWindow() {
        Window window = getWindow();
        window.setStatusBarColor(Color.TRANSPARENT);
        window.setNavigationBarColor(Color.TRANSPARENT);
        window.setNavigationBarDividerColor(Color.TRANSPARENT);
        window.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
    }

    private void applySystemBarAppearance() {
        View decorView = getWindow().getDecorView();
        decorView.post(() -> {
            WindowInsetsController controller = decorView.getWindowInsetsController();
            if (controller == null) return;
            int appearance =
                WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                    | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
            controller.setSystemBarsAppearance(appearance, appearance);
        });
    }

    private View buildContent() {
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(WHITE);
        page.setPadding(dp(16), dp(8), dp(16), dp(30));

        page.addView(buildHeader(), matchWrap(0, dp(20)));

        page.addView(sectionHeader(
            "Voice and conversation",
            "Choose Jarvis's voice, response mode and follow-up behaviour."
        ), matchWrap(0, dp(10)));
        page.addView(buildVoiceCard(), matchWrap(0, dp(24)));

        page.addView(sectionHeader(
            "Wake word and background",
            "Keep the dedicated offline detector available when the chat is closed."
        ), matchWrap(0, dp(10)));
        page.addView(buildWakeCard(), matchWrap(0, dp(24)));

        page.addView(sectionHeader(
            "Assistant and overlay",
            "Control the Side button, compact overlay and background conversation."
        ), matchWrap(0, dp(10)));
        page.addView(buildAssistantCard(), matchWrap(0, dp(12)));
        page.addView(buildBehaviourCard(), matchWrap(0, dp(24)));

        page.addView(sectionHeader(
            "Connections",
            "Private Jarvis Core and optional Home Assistant voice credentials."
        ), matchWrap(0, dp(10)));
        page.addView(buildCoreCard(), matchWrap(0, dp(12)));
        page.addView(buildHomeAssistantCard(), matchWrap(0, dp(24)));

        page.addView(buildIntegrationsSection(), matchWrap(0, dp(24)));

        page.addView(sectionHeader(
            "Developer",
            "Review Jarvis improvement proposals and protected developer controls."
        ), matchWrap(0, dp(10)));
        page.addView(buildDeveloperCard(), matchWrap(0, dp(24)));

        page.addView(sectionHeader(
            "Updates",
            "Secure Android updates, release channels and rollback information."
        ), matchWrap(0, dp(10)));
        page.addView(buildUpdatesCard(), matchWrap(0, dp(24)));

        page.addView(sectionHeader(
            "Diagnostics",
            "Connection, microphone, wake and response-performance checks."
        ), matchWrap(0, dp(10)));
        page.addView(buildVoiceFoundationCard(), matchWrap(0, dp(24)));

        Button save = primaryButton("Save changes");
        save.setOnClickListener(view -> saveSettings());
        page.addView(save, matchWrap());

        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setClipToPadding(false);
        scroll.addView(page, new ScrollView.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        scroll.setOnApplyWindowInsetsListener((view, insets) -> {
            Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
            Insets ime = insets.getInsets(WindowInsets.Type.ime());
            page.setPadding(
                dp(16) + bars.left,
                dp(8) + bars.top,
                dp(16) + bars.right,
                dp(30) + Math.max(bars.bottom, ime.bottom)
            );
            return insets;
        });
        scroll.requestApplyInsets();
        return scroll;
    }

    private View buildUpdatesCard() {
        LinearLayout card = card();
        TextView version = note("Current version: " + JarvisVersion.RELEASE);
        card.addView(version, matchWrap(0, dp(12)));
        Button updates = primaryButton("Open Jarvis updates");
        updates.setOnClickListener(view -> startActivity(new Intent(this, UpdatesActivity.class)));
        card.addView(updates, matchWrap());
        return card;
    }

    private View buildIntegrationsSection() {
        LinearLayout section = new LinearLayout(this);
        section.setOrientation(LinearLayout.VERTICAL);
        section.setTag(INTEGRATIONS_SECTION_TAG);

        LinearLayout heading = (LinearLayout) sectionHeader(
            "Integrations",
            "Google, email, calendar, contacts and external services."
        );
        heading.setPadding(dp(14), dp(13), dp(14), dp(13));
        heading.setBackground(rounded(SOFT, 18, 1, LINE));
        heading.setClickable(true);
        heading.setFocusable(true);

        TextView disclosure = note("Show integrations");
        disclosure.setPadding(0, dp(8), 0, 0);
        heading.addView(disclosure, matchWrap());

        LinearLayout content = card();
        content.setTag(INTEGRATIONS_CONTENT_TAG);
        content.setVisibility(View.GONE);
        Button integrations = secondaryButton("Open integrations & accounts");
        integrations.setTag(INTEGRATIONS_BUTTON_TAG);
        integrations.setOnClickListener(view ->
            startActivity(new Intent(this, IntegrationsActivity.class))
        );
        content.addView(integrations, matchWrap());

        heading.setContentDescription(
            "Integrations. Google, email, calendar, contacts and external services. Collapsed."
        );
        heading.setOnClickListener(view -> {
            boolean expanding = content.getVisibility() != View.VISIBLE;
            content.setVisibility(expanding ? View.VISIBLE : View.GONE);
            disclosure.setText(expanding ? "Hide integrations" : "Show integrations");
            heading.setContentDescription(
                "Integrations. Google, email, calendar, contacts and external services. "
                    + (expanding ? "Expanded." : "Collapsed.")
            );
        });

        section.addView(heading, matchWrap());
        section.addView(content, matchWrap(dp(10), 0));
        return section;
    }

    private View buildDeveloperCard() {
        LinearLayout card = card();
        Button improvements = secondaryButton("Open improvements");
        improvements.setOnClickListener(view ->
            startActivity(new Intent(this, ImprovementsActivity.class))
        );
        card.addView(improvements, matchWrap());
        return card;
    }

    private View buildHeader() {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);

        ImageButton back = iconButton(R.drawable.ic_back, "Back");
        back.setOnClickListener(view -> finish());
        row.addView(back, new LinearLayout.LayoutParams(dp(42), dp(42)));

        LinearLayout titleBlock = new LinearLayout(this);
        titleBlock.setOrientation(LinearLayout.VERTICAL);
        titleBlock.setPadding(dp(12), 0, 0, 0);
        TextView title = text("Settings", 25, BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        titleBlock.addView(title, matchWrap());
        TextView subtitle = text("Jarvis Android assistant", 13, MID);
        subtitle.setPadding(0, dp(2), 0, 0);
        titleBlock.addView(subtitle, matchWrap());
        row.addView(titleBlock, new LinearLayout.LayoutParams(
            0,
            ViewGroup.LayoutParams.WRAP_CONTENT,
            1f
        ));
        return row;
    }

    private View buildAssistantCard() {
        LinearLayout card = card();
        assistantStatus = statusBadge("Checking default assistant…");
        card.addView(assistantStatus, matchWrap(0, dp(14)));

        Button defaults = primaryButton("Open default assistant settings");
        defaults.setOnClickListener(view -> openDefaultAssistantSettings());
        card.addView(defaults, matchWrap(0, dp(10)));

        Button battery = secondaryButton("Open battery optimisation settings");
        battery.setOnClickListener(view -> openBatterySettings());
        card.addView(battery, matchWrap());

        TextView note = note(
            "In Android Settings, open Digital assistant app and select Jarvis. "
                + "Set Jarvis battery use to Unrestricted for the best always-on result."
        );
        card.addView(note, matchWrap(dp(14), 0));
        return card;
    }

    private View buildWakeCard() {
        LinearLayout card = card();

        wakeEnabled = new Switch(this);
        dedicatedWake = new Switch(this);
        assistantWakeAlways = new Switch(this);

        card.addView(toggleRow(
            "Wake word",
            "Listen for Jarvis when voice mode is closed.",
            wakeEnabled
        ), matchWrap());
        card.addView(divider(), matchWrap());
        card.addView(toggleRow(
            "Dedicated detector",
            "Sherpa-ONNX runs fully on the phone and listens only for the Jarvis keyword.",
            dedicatedWake
        ), matchWrap());
        card.addView(divider(), matchWrap());
        card.addView(toggleRow(
            "Always on as default assistant",
            "Keep the detector armed while Jarvis is Android's selected assistant.",
            assistantWakeAlways
        ), matchWrap(0, dp(14)));

        wakeEngineStatus = statusBadge("Checking wake-word engine…");
        card.addView(wakeEngineStatus, matchWrap(0, dp(14)));

        wakePhrase = field("Wake phrase", false);
        card.addView(fieldGroup("Wake phrase", wakePhrase), matchWrap(0, dp(14)));

        wakeSensitivity = spinner(List.of(
            "Balanced",
            "More sensitive",
            "Fewer false wakes"
        ));
        card.addView(choiceGroup("Detection sensitivity", wakeSensitivity), matchWrap(0, dp(12)));

        TextView note = note(
            "The dedicated detector runs fully offline and needs no account "
                + "or credential. Android requires a silent foreground-service "
                + "disclosure while the microphone remains active."
        );
        card.addView(note, matchWrap(dp(14), dp(14)));

        Button notificationSettings = secondaryButton(
            "Wake-word notification settings"
        );
        notificationSettings.setOnClickListener(
            view -> openWakeNotificationSettings()
        );
        card.addView(notificationSettings, matchWrap());

        dedicatedWake.setOnCheckedChangeListener(
            (button, checked) -> updateWakeControls()
        );
        return card;
    }

    private View buildVoiceCard() {
        LinearLayout card = card();

        conversationMode = spinner(List.of("Standard — recommended", "Live — experimental"));
        card.addView(choiceGroup("Conversation mode", conversationMode), matchWrap(0, dp(14)));

        voice = spinner(VoiceCatalog.labels());
        card.addView(choiceGroup("Voice", voice), matchWrap(0, dp(14)));

        responsiveness = spinner(List.of("Fast", "Balanced", "Patient"));
        card.addView(choiceGroup("Turn detection", responsiveness), matchWrap(0, dp(14)));

        keepOpen = new Switch(this);
        standardAutoListen = new Switch(this);
        card.addView(toggleRow(
            "Continuous conversation",
            "Keep listening until you end voice mode.",
            keepOpen
        ), matchWrap());
        card.addView(divider(), matchWrap());
        card.addView(toggleRow(
            "Standard mode follow-up",
            "Listen again automatically after Jarvis finishes speaking.",
            standardAutoListen
        ), matchWrap());
        return card;
    }


    private View buildVoiceFoundationCard() {
        LinearLayout card = card();

        voiceFoundationStatus = note(
            new VoiceDiagnosticsStore(this).summary()
        );
        voiceFoundationStatus.setTextIsSelectable(true);
        card.addView(
            voiceFoundationStatus,
            matchWrap(0, dp(12))
        );

        Button refresh = secondaryButton(
            "Refresh voice diagnostics"
        );
        refresh.setOnClickListener(view ->
            updateVoiceFoundationStatus()
        );
        card.addView(refresh, matchWrap(0, dp(10)));

        Button systemTest = primaryButton(
            "Run Jarvis system test"
        );
        systemTest.setOnClickListener(view ->
            runJarvisSystemTest()
        );
        card.addView(systemTest, matchWrap(0, dp(10)));

        Button resetDiagnostics = secondaryButton(
            "Reset diagnostics counters"
        );
        resetDiagnostics.setOnClickListener(view -> {
            new VoiceDiagnosticsStore(this).resetCounters();
            updateVoiceFoundationStatus();
            Toast.makeText(
                this,
                "Diagnostics counters reset",
                Toast.LENGTH_SHORT
            ).show();
        });
        card.addView(resetDiagnostics, matchWrap());

        return card;
    }

    private void updateVoiceFoundationStatus() {
        if (voiceFoundationStatus == null) return;
        voiceFoundationStatus.setText(
            new VoiceDiagnosticsStore(this).summary()
        );
    }

    private void runJarvisSystemTest() {
        if (voiceFoundationStatus == null) return;
        voiceFoundationStatus.setText(
            "Running Jarvis system test…"
        );
        String configured = coreUrl == null
            ? ""
            : coreUrl.getText().toString();
        String configuredRemote = remoteCoreUrl == null
            ? ""
            : remoteCoreUrl.getText().toString();
        new JarvisSystemTest(this).run(configured, configuredRemote, result -> {
            voiceFoundationStatus.setText(
                result.report + "\n\n"
                    + new VoiceDiagnosticsStore(this).summary()
            );
            Toast.makeText(
                this,
                result.passed
                    ? "Jarvis system test passed"
                    : "Jarvis system test needs attention",
                Toast.LENGTH_LONG
            ).show();
        });
    }

    private View buildBehaviourCard() {
        LinearLayout card = card();

        assistantOverlay = new Switch(this);
        assistantStartVoice = new Switch(this);
        startWithVoice = new Switch(this);
        backgroundConversations = new Switch(this);

        card.addView(toggleRow(
            "Compact assistant overlay",
            "Show Jarvis above the current app instead of opening full chat.",
            assistantOverlay
        ), matchWrap());
        card.addView(divider(), matchWrap());
        card.addView(toggleRow(
            "Listen when overlay opens",
            "Start the microphone immediately after Side-button invocation.",
            assistantStartVoice
        ), matchWrap());
        card.addView(divider(), matchWrap());
        card.addView(toggleRow(
            "Start voice when Jarvis opens",
            "Begin voice mode when the full app is opened.",
            startWithVoice
        ), matchWrap());
        card.addView(divider(), matchWrap());
        card.addView(toggleRow(
            "Background conversations",
            "Allow an active voice conversation to continue outside the app.",
            backgroundConversations
        ), matchWrap());
        return card;
    }

    private View buildCoreCard() {
        LinearLayout card = card();
        coreUrl = field("http://192.168.1.40:8000", false);
        remoteCoreUrl = field("Remote HTTPS or Tailscale URL", false);
        mobileToken = field("Mobile voice token", true);
        userName = field("Aaron", false);
        card.addView(fieldGroup("Jarvis Core LAN URL", coreUrl), matchWrap(0, dp(14)));
        card.addView(fieldGroup("Remote Core URL — optional", remoteCoreUrl), matchWrap(0, dp(14)));
        card.addView(fieldGroup("Mobile voice token", mobileToken), matchWrap(0, dp(14)));
        card.addView(fieldGroup("Your name", userName), matchWrap());
        return card;
    }

    private View buildHomeAssistantCard() {
        LinearLayout card = card();
        homeAssistantUrl = field("Home Assistant URL", false);
        homeAssistantToken = field("Home Assistant long-lived token", true);
        pipeline = field("Assist pipeline ID — optional", false);
        card.addView(fieldGroup("Home Assistant URL", homeAssistantUrl), matchWrap(0, dp(14)));
        card.addView(fieldGroup("Long-lived token", homeAssistantToken), matchWrap(0, dp(14)));
        card.addView(fieldGroup("Assist pipeline ID", pipeline), matchWrap());
        return card;
    }

    private void loadSettings() {
        coreUrl.setText(store.coreUrl());
        remoteCoreUrl.setText(store.remoteCoreUrl());
        userName.setText(store.userName());
        conversationMode.setSelection(
            ConversationMode.STANDARD.equals(store.conversationMode()) ? 0 : 1
        );
        voice.setSelection(VoiceCatalog.indexOf(store.voiceId()));
        responsiveness.setSelection(switch (store.vadEagerness()) {
            case "medium" -> 1;
            case "low" -> 2;
            default -> 0;
        });
        wakeSensitivity.setSelection(sensitivityPosition(store.wakeSensitivity()));

        keepOpen.setChecked(store.keepConversationOpen());
        standardAutoListen.setChecked(store.standardAutoListen());
        wakeEnabled.setChecked(store.wakeEnabled());
        dedicatedWake.setChecked(store.dedicatedWakeEnabled());
        wakePhrase.setText(store.wakePhrase());
        backgroundConversations.setChecked(store.backgroundConversations());
        startWithVoice.setChecked(store.startWithVoice());
        assistantWakeAlways.setChecked(store.assistantWakeAlways());
        assistantOverlay.setChecked(store.assistantOverlayEnabled());
        assistantStartVoice.setChecked(store.assistantStartsVoice());

        homeAssistantUrl.setText(store.homeAssistantUrl());
        pipeline.setText(store.homeAssistantPipeline());

        if (store.hasMobileToken()) {
            mobileToken.setHint("Saved securely — leave blank to keep it");
        }
        if (store.hasHomeAssistantToken()) {
            homeAssistantToken.setHint("Saved securely — leave blank to keep it");
        }


        updateAssistantStatus();
        updateVoiceFoundationStatus();
        updateWakeControls();
    }

    private void saveSettings() {
        try {
            CoreUrl.validateBase(coreUrl.getText().toString());
            String remoteUrl = remoteCoreUrl.getText().toString().trim();
            if (!remoteUrl.isBlank()) CoreUrl.validateBase(remoteUrl);

            VoiceCatalog.Entry selectedVoice = VoiceCatalog.at(voice.getSelectedItemPosition());
            String selectedMode = conversationMode.getSelectedItemPosition() == 0
                ? ConversationMode.STANDARD
                : ConversationMode.LIVE;
            String eagerness = switch (responsiveness.getSelectedItemPosition()) {
                case 1 -> "medium";
                case 2 -> "low";
                default -> "high";
            };

            store.saveProduct(
                coreUrl.getText().toString(),
                mobileToken.getText().toString(),
                userName.getText().toString(),
                selectedMode,
                selectedVoice.id,
                eagerness,
                keepOpen.isChecked(),
                standardAutoListen.isChecked(),
                wakeEnabled.isChecked(),
                wakePhrase.getText().toString(),
                backgroundConversations.isChecked(),
                startWithVoice.isChecked(),
                homeAssistantUrl.getText().toString(),
                homeAssistantToken.getText().toString(),
                pipeline.getText().toString()
            );
            store.setRemoteCoreUrl(remoteUrl);
            store.setAssistantOptions(
                assistantWakeAlways.isChecked(),
                assistantOverlay.isChecked(),
                assistantStartVoice.isChecked()
            );
            store.setWakeWordOptions(
                dedicatedWake.isChecked(),
                selectedSensitivity()
            );

            mobileToken.setText("");
            homeAssistantToken.setText("");
            loadSettings();
            applySavedRuntimeSettings();

            Toast.makeText(
                this,
                "Settings saved",
                Toast.LENGTH_LONG
            ).show();
        } catch (Exception exception) {
            Toast.makeText(
                this,
                "Could not save settings: " + safeMessage(exception),
                Toast.LENGTH_LONG
            ).show();
        }
    }

    private void updateWakeControls() {
        if (dedicatedWake == null) return;

        boolean dedicated = dedicatedWake.isChecked();

        wakePhrase.setEnabled(!dedicated);
        wakePhrase.setAlpha(dedicated ? 0.55f : 1f);

        wakeSensitivity.setEnabled(dedicated);
        wakeSensitivity.setAlpha(dedicated ? 1f : 0.55f);

        if (dedicated) {
            wakePhrase.setText("jarvis");
            setStatusBadge(
                wakeEngineStatus,
                "Dedicated offline Jarvis detector ready",
                true
            );
        } else {
            setStatusBadge(
                wakeEngineStatus,
                "Android speech-recognition fallback selected",
                false
            );
        }
    }

    private void updateAssistantStatus() {
        boolean active = JarvisVoiceInteractionService.isActiveAssistant(this);
        setStatusBadge(
            assistantStatus,
            active
                ? "Jarvis is the default Android assistant"
                : "Jarvis is not the default Android assistant",
            active
        );
    }

    private void openWakeNotificationSettings() {
        try {
            startActivity(
                new Intent(Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS)
                    .putExtra(
                        Settings.EXTRA_APP_PACKAGE,
                        getPackageName()
                    )
                    .putExtra(
                        Settings.EXTRA_CHANNEL_ID,
                        VoiceService.WAKE_NOTIFICATION_CHANNEL_ID
                    )
            );
        } catch (Exception exception) {
            startActivity(
                new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                    .putExtra(
                        Settings.EXTRA_APP_PACKAGE,
                        getPackageName()
                    )
            );
        }
    }

    private void openDefaultAssistantSettings() {
        Intent defaults = new Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS);
        if (defaults.resolveActivity(getPackageManager()) != null) {
            startActivity(defaults);
            return;
        }

        try {
            RoleManager roles = getSystemService(RoleManager.class);
            if (roles != null && roles.isRoleAvailable(RoleManager.ROLE_ASSISTANT)) {
                startActivityForResult(
                    roles.createRequestRoleIntent(RoleManager.ROLE_ASSISTANT),
                    REQUEST_ASSISTANT_ROLE
                );
                return;
            }
        } catch (Exception ignored) {}

        try {
            startActivity(new Intent(Settings.ACTION_VOICE_INPUT_SETTINGS));
        } catch (Exception exception) {
            Toast.makeText(
                this,
                "Open Settings → Apps → Choose default apps → Digital assistant app",
                Toast.LENGTH_LONG
            ).show();
        }
    }

    private void openBatterySettings() {
        try {
            startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));
        } catch (Exception exception) {
            Intent details = new Intent(
                Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.parse("package:" + getPackageName())
            );
            startActivity(details);
        }
    }

    private void applySavedRuntimeSettings() {
        JarvisVoiceInteractionService.refreshWakeIfActive(this);

        boolean microphoneGranted =
            checkSelfPermission(android.Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED;
        boolean assistantHostsWake =
            store.assistantWakeAlways()
                && JarvisVoiceInteractionService.isActiveAssistant(this);

        if (store.wakeEnabled() && !microphoneGranted) {
            requestPermissions(
                new String[] { android.Manifest.permission.RECORD_AUDIO },
                REQUEST_WAKE_PERMISSION
            );
            return;
        }

        String action =
            store.wakeEnabled() && !assistantHostsWake
                ? VoiceService.ACTION_ARM_WAKE
                : VoiceService.ACTION_APPLY_SETTINGS;
        startForegroundService(
            new Intent(this, VoiceService.class).setAction(action)
        );
    }

    @Override public void onRequestPermissionsResult(
        int requestCode,
        String[] permissions,
        int[] grantResults
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != REQUEST_WAKE_PERMISSION) return;
        if (grantResults.length > 0
                && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            applySavedRuntimeSettings();
        } else {
            Toast.makeText(
                this,
                "Wake word needs microphone permission",
                Toast.LENGTH_LONG
            ).show();
        }
    }

    @Override protected void onResume() {
        super.onResume();
        if (assistantStatus != null) updateAssistantStatus();
    }

    @Override protected void onActivityResult(
        int requestCode,
        int resultCode,
        Intent data
    ) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQUEST_ASSISTANT_ROLE) {
            updateAssistantStatus();
            JarvisVoiceInteractionService.refreshWakeIfActive(this);
        }
    }

    private void clearChat() {
        startForegroundService(
            new Intent(this, VoiceService.class)
                .setAction(VoiceService.ACTION_NEW_CHAT)
        );
        Toast.makeText(
            this,
            "New chat started",
            Toast.LENGTH_SHORT
        ).show();
    }

    private View sectionHeader(String titleValue, String noteValue) {
        LinearLayout block = new LinearLayout(this);
        block.setOrientation(LinearLayout.VERTICAL);
        TextView title = text(titleValue, 17, BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        block.addView(title, matchWrap());
        TextView note = text(noteValue, 13, MID);
        note.setLineSpacing(0f, 1.12f);
        note.setPadding(0, dp(4), 0, 0);
        block.addView(note, matchWrap());
        return block;
    }

    private LinearLayout card() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(16), dp(16), dp(16), dp(16));
        card.setBackground(rounded(SOFT, 20, 1, LINE));
        return card;
    }

    private View toggleRow(String titleValue, String noteValue, Switch control) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(0, dp(10), 0, dp(10));

        LinearLayout copy = new LinearLayout(this);
        copy.setOrientation(LinearLayout.VERTICAL);
        TextView title = text(titleValue, 15, BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        copy.addView(title, matchWrap());
        TextView note = text(noteValue, 12, MID);
        note.setLineSpacing(0f, 1.08f);
        note.setPadding(0, dp(3), dp(12), 0);
        copy.addView(note, matchWrap());
        row.addView(copy, new LinearLayout.LayoutParams(
            0,
            ViewGroup.LayoutParams.WRAP_CONTENT,
            1f
        ));

        control.setText("");
        control.setShowText(false);
        control.setMinWidth(0);
        row.addView(control, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        return row;
    }

    private View fieldGroup(String labelValue, EditText value) {
        LinearLayout group = new LinearLayout(this);
        group.setOrientation(LinearLayout.VERTICAL);
        TextView label = text(labelValue, 13, MID);
        label.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        group.addView(label, matchWrap(0, dp(7)));
        group.addView(value, matchWrap());
        return group;
    }

    private View choiceGroup(String labelValue, Spinner value) {
        LinearLayout group = new LinearLayout(this);
        group.setOrientation(LinearLayout.VERTICAL);
        TextView label = text(labelValue, 13, MID);
        label.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        group.addView(label, matchWrap(0, dp(7)));
        group.addView(value, matchWrap());
        return group;
    }

    private Spinner spinner(List<String> values) {
        Spinner spinner = new Spinner(this);
        ArrayAdapter<String> adapter = new ArrayAdapter<>(
            this,
            android.R.layout.simple_spinner_item,
            values
        );
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        spinner.setAdapter(adapter);
        spinner.setPadding(dp(12), dp(6), dp(12), dp(6));
        spinner.setMinimumHeight(dp(50));
        spinner.setBackground(rounded(WHITE, 14, 1, LINE));
        return spinner;
    }

    private EditText field(String hint, boolean password) {
        EditText value = new EditText(this);
        value.setHint(hint);
        value.setHintTextColor(Color.rgb(135, 135, 135));
        value.setTextColor(BLACK);
        value.setTextSize(15);
        value.setSingleLine(true);
        value.setMinHeight(dp(50));
        value.setPadding(dp(13), dp(10), dp(13), dp(10));
        value.setBackground(rounded(WHITE, 14, 1, LINE));
        if (password) {
            value.setInputType(
                InputType.TYPE_CLASS_TEXT
                    | InputType.TYPE_TEXT_VARIATION_PASSWORD
            );
        } else {
            value.setInputType(
                InputType.TYPE_CLASS_TEXT
                    | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            );
        }
        return value;
    }

    private TextView statusBadge(String value) {
        TextView badge = text(value, 13, MID);
        badge.setGravity(Gravity.CENTER_VERTICAL);
        badge.setPadding(dp(12), dp(9), dp(12), dp(9));
        badge.setBackground(rounded(WHITE, 14, 1, LINE));
        return badge;
    }

    private void setStatusBadge(TextView badge, String value, boolean active) {
        if (badge == null) return;
        badge.setText(value);
        badge.setTextColor(active ? WHITE : MID);
        badge.setBackground(rounded(active ? BLACK : WHITE, 14, 1, active ? BLACK : LINE));
    }

    private TextView note(String value) {
        TextView note = text(value, 12, MID);
        note.setLineSpacing(0f, 1.12f);
        return note;
    }

    private Button primaryButton(String label) {
        Button value = new Button(this);
        value.setText(label);
        value.setAllCaps(false);
        value.setTextSize(15);
        value.setTextColor(WHITE);
        value.setMinHeight(dp(52));
        value.setBackground(rounded(BLACK, 18, 0, Color.TRANSPARENT));
        return value;
    }

    private Button secondaryButton(String label) {
        Button value = new Button(this);
        value.setText(label);
        value.setAllCaps(false);
        value.setTextSize(15);
        value.setTextColor(BLACK);
        value.setMinHeight(dp(52));
        value.setBackground(rounded(WHITE, 18, 1, LINE));
        return value;
    }

    private ImageButton iconButton(int icon, String description) {
        ImageButton value = new ImageButton(this);
        value.setImageResource(icon);
        value.setColorFilter(BLACK);
        value.setContentDescription(description);
        value.setPadding(dp(10), dp(10), dp(10), dp(10));
        value.setBackground(rounded(SOFT, 21, 0, Color.TRANSPARENT));
        return value;
    }

    private View divider() {
        View divider = new View(this);
        divider.setBackgroundColor(LINE);
        divider.setMinimumHeight(dp(1));
        return divider;
    }

    private TextView text(String value, int size, int colour) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(colour);
        view.setTypeface(Typeface.create("sans-serif", Typeface.NORMAL));
        return view;
    }

    private GradientDrawable rounded(
        int fill,
        int radiusDp,
        int strokeDp,
        int strokeColour
    ) {
        GradientDrawable background = new GradientDrawable();
        background.setColor(fill);
        background.setCornerRadius(dp(radiusDp));
        if (strokeDp > 0) background.setStroke(dp(strokeDp), strokeColour);
        return background;
    }

    private float selectedSensitivity() {
        return switch (wakeSensitivity.getSelectedItemPosition()) {
            case 1 -> 0.78f;
            case 2 -> 0.50f;
            default -> 0.65f;
        };
    }

    private int sensitivityPosition(float value) {
        if (value >= 0.72f) return 1;
        if (value <= 0.56f) return 2;
        return 0;
    }

    private LinearLayout.LayoutParams matchWrap() {
        return matchWrap(0, 0);
    }

    private LinearLayout.LayoutParams matchWrap(int top, int bottom) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        );
        params.topMargin = top;
        params.bottomMargin = bottom;
        return params;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private static String safeMessage(Exception exception) {
        String value = exception.getMessage();
        return value == null || value.isBlank()
            ? exception.getClass().getSimpleName()
            : value;
    }
}
