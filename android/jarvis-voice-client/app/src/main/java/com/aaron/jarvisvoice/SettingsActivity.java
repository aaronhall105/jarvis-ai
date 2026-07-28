package com.aaron.jarvisvoice;

import android.app.Activity;
import android.app.role.RoleManager;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import java.util.List;

public final class SettingsActivity extends Activity {
    private static final int BLACK = Color.rgb(20, 20, 20);
    private static final int MID = Color.rgb(105, 105, 105);
    private static final int LINE = Color.rgb(225, 225, 225);
    private static final int SOFT = Color.rgb(246, 246, 246);
    private static final int WHITE = Color.WHITE;

    private SecureStore store;
    private EditText coreUrl;
    private EditText mobileToken;
    private EditText userName;
    private Spinner conversationMode;
    private Spinner voice;
    private Spinner responsiveness;
    private Switch keepOpen;
    private Switch standardAutoListen;
    private Switch wakeEnabled;
    private EditText wakePhrase;
    private Switch backgroundConversations;
    private Switch startWithVoice;
    private Switch assistantWakeAlways;
    private Switch assistantOverlay;
    private Switch assistantStartVoice;
    private TextView assistantStatus;
    private EditText homeAssistantUrl;
    private EditText homeAssistantToken;
    private EditText pipeline;

    @Override protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        store = new SecureStore(this);
        configureWindow();
        setContentView(buildContent());
        loadSettings();
    }

    private void configureWindow() {
        Window window = getWindow();
        window.setStatusBarColor(WHITE);
        window.setNavigationBarColor(WHITE);
        window.getDecorView().setSystemUiVisibility(
            View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR
        );
    }

    private View buildContent() {
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(WHITE);
        page.setPadding(dp(16), dp(8), dp(16), dp(30));

        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);
        Button back = textButton("Back");
        back.setOnClickListener(view -> finish());
        top.addView(back, wrapWrap());
        TextView title = text("Settings", 22, BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        top.addView(title, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        page.addView(top, matchWrap(0, dp(14)));

        page.addView(sectionTitle("Default Android assistant"), matchWrap(0, dp(8)));
        LinearLayout assistantCard = card();
        assistantStatus = note("Checking assistant status…");
        assistantCard.addView(assistantStatus, matchWrap(0, dp(10)));
        assistantWakeAlways = toggle("Keep the Jarvis wake phrase armed whenever Jarvis is the default assistant");
        assistantOverlay = toggle("Show the compact Jarvis overlay instead of opening the full app");
        assistantStartVoice = toggle("Start listening immediately when the overlay opens");
        assistantCard.addView(assistantWakeAlways, matchWrap(0, dp(4)));
        assistantCard.addView(assistantOverlay, matchWrap(0, dp(4)));
        assistantCard.addView(assistantStartVoice, matchWrap(0, dp(10)));
        Button makeDefault = primaryButton("Set Jarvis as default assistant");
        makeDefault.setOnClickListener(view -> requestAssistantRole());
        assistantCard.addView(makeDefault, matchWrap(0, dp(8)));
        Button battery = secondaryButton("Open battery optimisation settings");
        battery.setOnClickListener(view -> openBatterySettings());
        assistantCard.addView(battery, matchWrap());
        assistantCard.addView(note(
            "The selected Android assistant service stays available for Side-button invocation and wake-phrase listening. Samsung may still require Jarvis to be set to Unrestricted battery use."
        ), matchWrap(dp(10), 0));
        page.addView(assistantCard, matchWrap(0, dp(18)));

        page.addView(sectionTitle("Voice experience"), matchWrap(0, dp(8)));
        LinearLayout voiceCard = card();
        voiceCard.addView(label("Mode"), matchWrap(0, dp(6)));
        conversationMode = spinner(List.of("Live", "Standard"));
        voiceCard.addView(conversationMode, matchWrap(0, dp(12)));
        voiceCard.addView(note(
            "Live keeps the microphone session open for natural interruption. Standard listens one message at a time and can automatically listen again after Jarvis finishes."
        ), matchWrap(0, dp(14)));

        voiceCard.addView(label("Voice"), matchWrap(0, dp(6)));
        voice = spinner(VoiceCatalog.labels());
        voiceCard.addView(voice, matchWrap(0, dp(12)));

        voiceCard.addView(label("Turn detection"), matchWrap(0, dp(6)));
        responsiveness = spinner(List.of("Fast", "Balanced", "Patient"));
        voiceCard.addView(responsiveness, matchWrap(0, dp(12)));

        keepOpen = toggle("Keep the conversation open until I end it");
        standardAutoListen = toggle("Standard mode automatically listens again");
        startWithVoice = toggle("Start voice when the app opens");
        backgroundConversations = toggle("Allow voice while the app is in the background");
        voiceCard.addView(keepOpen, matchWrap(0, dp(4)));
        voiceCard.addView(standardAutoListen, matchWrap(0, dp(4)));
        voiceCard.addView(startWithVoice, matchWrap(0, dp(4)));
        voiceCard.addView(backgroundConversations, matchWrap());
        page.addView(voiceCard, matchWrap(0, dp(18)));

        page.addView(sectionTitle("Wake word"), matchWrap(0, dp(8)));
        LinearLayout wakeCard = card();
        wakeEnabled = toggle("Enable the Jarvis wake phrase");
        wakeCard.addView(wakeEnabled, matchWrap(0, dp(8)));
        wakePhrase = field("Wake phrase", false);
        wakeCard.addView(wakePhrase, matchWrap());
        wakeCard.addView(note(
            "When Jarvis is selected as the default assistant, Android keeps its VoiceInteractionService available and Jarvis continually rearms the on-device recogniser. A dedicated DSP wake-word model is not bundled, so reliability still depends on Samsung's recogniser and battery policy."
        ), matchWrap(dp(10), 0));
        page.addView(wakeCard, matchWrap(0, dp(18)));

        page.addView(sectionTitle("Jarvis Core"), matchWrap(0, dp(8)));
        LinearLayout coreCard = card();
        coreUrl = field("Jarvis Core URL", false);
        mobileToken = field("Mobile voice token", true);
        userName = field("Your name", false);
        coreCard.addView(coreUrl, matchWrap(0, dp(10)));
        coreCard.addView(mobileToken, matchWrap(0, dp(10)));
        coreCard.addView(userName, matchWrap());
        page.addView(coreCard, matchWrap(0, dp(18)));

        page.addView(sectionTitle("Original Jarvis voice"), matchWrap(0, dp(8)));
        LinearLayout haCard = card();
        haCard.addView(note(
            "Only needed when Jarvis — Home Assistant original voice is selected."
        ), matchWrap(0, dp(10)));
        homeAssistantUrl = field("Home Assistant URL", false);
        homeAssistantToken = field("Home Assistant long-lived token", true);
        pipeline = field("Assist pipeline ID — optional", false);
        haCard.addView(homeAssistantUrl, matchWrap(0, dp(10)));
        haCard.addView(homeAssistantToken, matchWrap(0, dp(10)));
        haCard.addView(pipeline, matchWrap());
        page.addView(haCard, matchWrap(0, dp(18)));

        Button save = primaryButton("Save settings");
        save.setOnClickListener(view -> saveSettings());
        page.addView(save, matchWrap(0, dp(10)));

        Button clear = secondaryButton("Clear chat history and start a new chat");
        clear.setOnClickListener(view -> clearChat());
        page.addView(clear, matchWrap());

        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.addView(page, new ScrollView.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        ));
        return scroll;
    }

    private void loadSettings() {
        coreUrl.setText(store.coreUrl());
        userName.setText(store.userName());
        conversationMode.setSelection(ConversationMode.STANDARD.equals(store.conversationMode()) ? 1 : 0);
        voice.setSelection(VoiceCatalog.indexOf(store.voiceId()));
        responsiveness.setSelection(switch (store.vadEagerness()) {
            case "medium" -> 1;
            case "low" -> 2;
            default -> 0;
        });
        keepOpen.setChecked(store.keepConversationOpen());
        standardAutoListen.setChecked(store.standardAutoListen());
        wakeEnabled.setChecked(store.wakeEnabled());
        wakePhrase.setText(store.wakePhrase());
        backgroundConversations.setChecked(store.backgroundConversations());
        startWithVoice.setChecked(store.startWithVoice());
        assistantWakeAlways.setChecked(store.assistantWakeAlways());
        assistantOverlay.setChecked(store.assistantOverlayEnabled());
        assistantStartVoice.setChecked(store.assistantStartsVoice());
        updateAssistantStatus();
        homeAssistantUrl.setText(store.homeAssistantUrl());
        pipeline.setText(store.homeAssistantPipeline());
        if (store.hasMobileToken()) {
            mobileToken.setHint("Mobile voice token saved securely — leave blank to keep it");
        }
        if (store.hasHomeAssistantToken()) {
            homeAssistantToken.setHint("Home Assistant token saved securely — leave blank to keep it");
        }
    }

    private void saveSettings() {
        try {
            VoiceCatalog.Entry selectedVoice = VoiceCatalog.at(voice.getSelectedItemPosition());
            String selectedMode = conversationMode.getSelectedItemPosition() == 1
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
            store.setAssistantOptions(
                assistantWakeAlways.isChecked(),
                assistantOverlay.isChecked(),
                assistantStartVoice.isChecked()
            );
            mobileToken.setText("");
            homeAssistantToken.setText("");
            loadSettings();
            if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) == android.content.pm.PackageManager.PERMISSION_GRANTED) {
                startForegroundService(new Intent(this, VoiceService.class).setAction(VoiceService.ACTION_APPLY_SETTINGS));
            }
            Toast.makeText(this, "Settings saved", Toast.LENGTH_SHORT).show();
        } catch (Exception exception) {
            Toast.makeText(this, "Could not save settings: " + safeMessage(exception), Toast.LENGTH_LONG).show();
        }
    }

    @Override protected void onResume() {
        super.onResume();
        if (assistantStatus != null) updateAssistantStatus();
    }

    private void updateAssistantStatus() {
        boolean active = JarvisVoiceInteractionService.isActiveAssistant(this);
        assistantStatus.setText(active
            ? "Jarvis is the current default assistant. Hold the Side button to open the overlay."
            : "Jarvis is not currently the default assistant.");
    }

    private void requestAssistantRole() {
        try {
            RoleManager roles = getSystemService(RoleManager.class);
            if (roles != null && roles.isRoleAvailable(RoleManager.ROLE_ASSISTANT)) {
                startActivityForResult(roles.createRequestRoleIntent(RoleManager.ROLE_ASSISTANT), 1810);
                return;
            }
        } catch (Exception ignored) { }
        try {
            startActivity(new Intent(Settings.ACTION_VOICE_INPUT_SETTINGS));
        } catch (Exception exception) {
            Toast.makeText(this, "Open Settings → Apps → Choose default apps → Digital assistant app", Toast.LENGTH_LONG).show();
        }
    }

    private void openBatterySettings() {
        try {
            startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));
        } catch (Exception exception) {
            startActivity(new Intent(Settings.ACTION_SETTINGS));
        }
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == 1810) updateAssistantStatus();
    }

    private void clearChat() {
        new ChatHistoryStore(this).clear();
        store.newConversationId();
        if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) == android.content.pm.PackageManager.PERMISSION_GRANTED) {
            startForegroundService(new Intent(this, VoiceService.class).setAction(VoiceService.ACTION_NEW_CHAT));
        }
        Toast.makeText(this, "New chat started", Toast.LENGTH_SHORT).show();
    }

    private LinearLayout card() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(14), dp(14), dp(14), dp(14));
        card.setBackground(rounded(WHITE, 18, 1, LINE));
        return card;
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
        spinner.setPadding(dp(10), dp(4), dp(10), dp(4));
        spinner.setBackground(rounded(SOFT, 12, 0, Color.TRANSPARENT));
        return spinner;
    }

    private EditText field(String hint, boolean password) {
        EditText value = new EditText(this);
        value.setHint(hint);
        value.setHintTextColor(Color.rgb(130, 130, 130));
        value.setTextColor(BLACK);
        value.setTextSize(15);
        value.setSingleLine(true);
        value.setPadding(dp(12), dp(10), dp(12), dp(10));
        value.setBackground(rounded(SOFT, 12, 0, Color.TRANSPARENT));
        if (password) {
            value.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        } else {
            value.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        }
        return value;
    }

    private Switch toggle(String label) {
        Switch value = new Switch(this);
        value.setText(label);
        value.setTextColor(BLACK);
        value.setTextSize(15);
        value.setShowText(false);
        value.setPadding(0, dp(3), 0, dp(3));
        return value;
    }

    private TextView sectionTitle(String value) {
        TextView title = text(value, 13, MID);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        return title;
    }

    private TextView label(String value) {
        TextView label = text(value, 14, BLACK);
        label.setTypeface(Typeface.DEFAULT_BOLD);
        return label;
    }

    private TextView note(String value) {
        TextView note = text(value, 13, MID);
        note.setLineSpacing(0f, 1.12f);
        return note;
    }

    private Button primaryButton(String label) {
        Button value = new Button(this);
        value.setText(label);
        value.setAllCaps(false);
        value.setTextSize(15);
        value.setTextColor(WHITE);
        value.setBackground(rounded(BLACK, 22, 0, Color.TRANSPARENT));
        return value;
    }

    private Button secondaryButton(String label) {
        Button value = new Button(this);
        value.setText(label);
        value.setAllCaps(false);
        value.setTextSize(15);
        value.setTextColor(BLACK);
        value.setBackground(rounded(WHITE, 22, 1, LINE));
        return value;
    }

    private Button textButton(String label) {
        Button value = new Button(this);
        value.setText(label);
        value.setAllCaps(false);
        value.setTextSize(14);
        value.setTextColor(BLACK);
        value.setMinWidth(0);
        value.setMinimumWidth(0);
        value.setPadding(0, dp(8), dp(16), dp(8));
        value.setBackgroundColor(Color.TRANSPARENT);
        return value;
    }

    private TextView text(String value, int size, int colour) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(colour);
        view.setTypeface(Typeface.create("sans-serif", Typeface.NORMAL));
        return view;
    }

    private GradientDrawable rounded(int fill, int radiusDp, int strokeDp, int strokeColour) {
        GradientDrawable background = new GradientDrawable();
        background.setColor(fill);
        background.setCornerRadius(dp(radiusDp));
        if (strokeDp > 0) background.setStroke(dp(strokeDp), strokeColour);
        return background;
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

    private LinearLayout.LayoutParams wrapWrap() {
        return new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        );
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private static String safeMessage(Exception exception) {
        String value = exception.getMessage();
        return value == null || value.isBlank() ? exception.getClass().getSimpleName() : value;
    }
}
