package com.aaron.jarvisvoice;

import android.Manifest;
import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.List;

public final class MainActivity extends Activity {
    private SecureStore store;
    private EditText coreUrl;
    private EditText mobileToken;
    private EditText userName;
    private Spinner voice;
    private CheckBox wakeEnabled;
    private EditText wakePhrase;
    private EditText homeAssistantUrl;
    private EditText homeAssistantToken;
    private EditText pipeline;
    private TextView status;

    private final BroadcastReceiver statusReceiver = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            if (VoiceService.ACTION_STATUS.equals(intent.getAction())) {
                status.setText(intent.getStringExtra(VoiceService.EXTRA_STATUS));
            }
        }
    };

    @Override protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        store = new SecureStore(this);
        setContentView(buildContent());
        loadSettings();
    }

    @Override protected void onStart() {
        super.onStart();
        IntentFilter filter = new IntentFilter(VoiceService.ACTION_STATUS);
        if (android.os.Build.VERSION.SDK_INT >= 33) {
            registerReceiver(statusReceiver, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(statusReceiver, filter);
        }
    }

    @Override protected void onStop() {
        try { unregisterReceiver(statusReceiver); } catch (Exception ignored) {}
        super.onStop();
    }

    private ScrollView buildContent() {
        int pad = dp(20);
        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(pad, pad, pad, pad);
        panel.setBackgroundColor(Color.rgb(8, 11, 13));

        TextView title = text("JARVIS UNIFIED VOICE", 27, Color.rgb(0, 229, 255));
        title.setGravity(Gravity.CENTER_HORIZONTAL);
        panel.addView(title, matchWrap());
        TextView subtitle = text("Android client v17.3.0", 14, Color.rgb(165, 181, 186));
        subtitle.setGravity(Gravity.CENTER_HORIZONTAL);
        panel.addView(subtitle, matchWrap(0, dp(24)));

        panel.addView(section("JARVIS CORE CONNECTION"), matchWrap(0, dp(8)));
        coreUrl = field("Jarvis Core URL — for example http://192.168.1.40:8000", false);
        mobileToken = field("Mobile voice token", true);
        userName = field("Your name", false);
        panel.addView(coreUrl, matchWrap(0, dp(10)));
        panel.addView(mobileToken, matchWrap(0, dp(10)));
        panel.addView(userName, matchWrap(0, dp(18)));

        panel.addView(section("VOICE"), matchWrap(0, dp(8)));
        voice = new Spinner(this);
        ArrayAdapter<String> voices = new ArrayAdapter<>(
            this,
            android.R.layout.simple_spinner_item,
            VoiceCatalog.labels()
        );
        voices.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        voice.setAdapter(voices);
        voice.setBackgroundColor(Color.rgb(17, 23, 27));
        panel.addView(voice, matchWrap(0, dp(18)));

        panel.addView(section("WAKE WORD"), matchWrap(0, dp(8)));
        wakeEnabled = new CheckBox(this);
        wakeEnabled.setText("Require wake word before a live conversation");
        wakeEnabled.setTextColor(Color.WHITE);
        panel.addView(wakeEnabled, matchWrap(0, dp(6)));
        wakePhrase = field("Wake phrase — Jarvis", false);
        panel.addView(wakePhrase, matchWrap(0, dp(18)));

        panel.addView(section("ORIGINAL JARVIS VOICE"), matchWrap(0, dp(8)));
        TextView originalNote = text(
            "Only required when the selected voice is “Jarvis — Home Assistant original voice”.",
            12,
            Color.rgb(165, 181, 186)
        );
        panel.addView(originalNote, matchWrap(0, dp(8)));
        homeAssistantUrl = field("Home Assistant URL — local or Nabu Casa", false);
        homeAssistantToken = field("Home Assistant long-lived access token", true);
        pipeline = field("Pipeline ID — leave blank for preferred", false);
        panel.addView(homeAssistantUrl, matchWrap(0, dp(10)));
        panel.addView(homeAssistantToken, matchWrap(0, dp(10)));
        panel.addView(pipeline, matchWrap(0, dp(18)));

        Button save = button("SAVE SETTINGS");
        save.setOnClickListener(view -> saveSettings());
        panel.addView(save, matchWrap(0, dp(10)));

        Button start = button("ARM / START JARVIS");
        start.setOnClickListener(view -> startJarvis());
        panel.addView(start, matchWrap(0, dp(10)));

        Button stop = button("STOP");
        stop.setTextColor(Color.rgb(255, 93, 115));
        stop.setOnClickListener(view -> startService(
            new Intent(this, VoiceService.class).setAction(VoiceService.ACTION_STOP)
        ));
        panel.addView(stop, matchWrap(0, dp(22)));

        status = text("Stopped", 16, Color.rgb(242, 247, 248));
        status.setPadding(dp(14), dp(14), dp(14), dp(14));
        status.setBackgroundColor(Color.rgb(17, 23, 27));
        panel.addView(status, matchWrap());

        TextView note = text(
            "Every completed request now goes through the existing Jarvis Core brain, including memory, people such as Amber, house awareness, routines, schedules and verified controls. Wake-word mode uses Android’s on-device recogniser when available. After Jarvis wakes, follow-up conversation stays open temporarily without repeating the wake word.",
            13,
            Color.rgb(165, 181, 186)
        );
        panel.addView(note, matchWrap(0, dp(18)));

        ScrollView scroll = new ScrollView(this);
        scroll.addView(panel);
        return scroll;
    }

    private void loadSettings() {
        coreUrl.setText(store.coreUrl());
        userName.setText(store.userName());
        voice.setSelection(VoiceCatalog.indexOf(store.voiceId()));
        wakeEnabled.setChecked(store.wakeEnabled());
        wakePhrase.setText(store.wakePhrase());
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
            VoiceCatalog.Entry selected = VoiceCatalog.at(voice.getSelectedItemPosition());
            store.saveUnified(
                coreUrl.getText().toString(),
                mobileToken.getText().toString(),
                userName.getText().toString(),
                selected.id,
                wakeEnabled.isChecked(),
                wakePhrase.getText().toString(),
                homeAssistantUrl.getText().toString(),
                homeAssistantToken.getText().toString(),
                pipeline.getText().toString()
            );
            mobileToken.setText("");
            homeAssistantToken.setText("");
            if (store.hasMobileToken()) {
                mobileToken.setHint("Mobile voice token saved securely — leave blank to keep it");
            }
            if (store.hasHomeAssistantToken()) {
                homeAssistantToken.setHint("Home Assistant token saved securely — leave blank to keep it");
            }
            Toast.makeText(this, "Settings saved", Toast.LENGTH_SHORT).show();
        } catch (Exception exception) {
            Toast.makeText(this, "Could not save settings: " + safeMessage(exception), Toast.LENGTH_LONG).show();
        }
    }

    private void startJarvis() {
        saveSettings();
        List<String> missing = new ArrayList<>();
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            missing.add(Manifest.permission.RECORD_AUDIO);
        }
        if (android.os.Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            missing.add(Manifest.permission.POST_NOTIFICATIONS);
        }
        if (!missing.isEmpty()) {
            requestPermissions(missing.toArray(new String[0]), 1730);
            return;
        }
        if (store.coreUrl().isBlank() || store.mobileToken().isBlank()) {
            Toast.makeText(this, "Jarvis Core URL and mobile voice token are required", Toast.LENGTH_LONG).show();
            return;
        }
        if (VoiceCatalog.isOriginal(store.voiceId()) &&
            (store.homeAssistantUrl().isBlank() || store.homeAssistantToken().isBlank())) {
            Toast.makeText(
                this,
                "The original Jarvis voice requires the Home Assistant URL and access token",
                Toast.LENGTH_LONG
            ).show();
            return;
        }
        startForegroundService(new Intent(this, VoiceService.class).setAction(VoiceService.ACTION_START));
    }

    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != 1730) return;
        for (int result : grantResults) {
            if (result != PackageManager.PERMISSION_GRANTED) {
                Toast.makeText(this, "Microphone permission is required", Toast.LENGTH_LONG).show();
                return;
            }
        }
        startJarvis();
    }

    private TextView section(String label) {
        return text(label, 13, Color.rgb(0, 229, 255));
    }

    private EditText field(String hint, boolean password) {
        EditText value = new EditText(this);
        value.setHint(hint);
        value.setHintTextColor(Color.rgb(120, 140, 146));
        value.setTextColor(Color.WHITE);
        value.setSingleLine(true);
        value.setPadding(dp(14), dp(10), dp(14), dp(10));
        value.setBackgroundColor(Color.rgb(17, 23, 27));
        if (password) {
            value.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        } else {
            value.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        }
        return value;
    }

    private Button button(String label) {
        Button value = new Button(this);
        value.setText(label);
        value.setTextColor(Color.WHITE);
        value.setBackgroundColor(Color.rgb(17, 23, 27));
        return value;
    }

    private TextView text(String value, int size, int color) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(color);
        return view;
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
        return value == null || value.isBlank() ? exception.getClass().getSimpleName() : value;
    }
}
