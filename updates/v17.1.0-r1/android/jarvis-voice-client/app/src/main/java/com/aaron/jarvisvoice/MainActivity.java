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
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.List;

public final class MainActivity extends Activity {
    private SecureStore store;
    private EditText url;
    private EditText token;
    private EditText pipeline;
    private EditText wake;
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
        unregisterReceiver(statusReceiver);
        super.onStop();
    }

    private ScrollView buildContent() {
        int pad = dp(20);
        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(pad, pad, pad, pad);
        panel.setBackgroundColor(Color.rgb(8, 11, 13));

        TextView title = text("JARVIS VOICE", 28, Color.rgb(0, 229, 255));
        title.setGravity(Gravity.CENTER_HORIZONTAL);
        panel.addView(title, matchWrap());
        TextView subtitle = text("Android client v17.1.0", 14, Color.rgb(165, 181, 186));
        subtitle.setGravity(Gravity.CENTER_HORIZONTAL);
        panel.addView(subtitle, matchWrap(0, dp(24)));

        url = field("Home Assistant URL — for example http://192.168.1.40:8123", false);
        token = field("Long-lived access token", true);
        pipeline = field("Pipeline ID — leave blank for preferred", false);
        wake = field("Wake phrase", false);
        panel.addView(url, matchWrap(0, dp(10)));
        panel.addView(token, matchWrap(0, dp(10)));
        panel.addView(pipeline, matchWrap(0, dp(10)));
        panel.addView(wake, matchWrap(0, dp(18)));

        Button save = button("SAVE SETTINGS");
        save.setOnClickListener(view -> saveSettings());
        panel.addView(save, matchWrap(0, dp(10)));

        Button start = button("START JARVIS VOICE");
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
            "Start the service while this app is open. During playback say “Jarvis, stop” or “Jarvis” followed by a replacement command. Android may require an offline English (UK) speech model for the best results.",
            13,
            Color.rgb(165, 181, 186)
        );
        panel.addView(note, matchWrap(0, dp(18)));

        ScrollView scroll = new ScrollView(this);
        scroll.addView(panel);
        return scroll;
    }

    private void loadSettings() {
        url.setText(store.baseUrl());
        pipeline.setText(store.pipeline());
        wake.setText(store.wakePhrase());
        if (store.hasToken()) token.setHint("Token saved securely — leave blank to keep it");
    }

    private void saveSettings() {
        try {
            store.save(
                url.getText().toString(),
                token.getText().toString(),
                pipeline.getText().toString(),
                wake.getText().toString()
            );
            token.setText("");
            token.setHint("Token saved securely — leave blank to keep it");
            Toast.makeText(this, "Settings saved", Toast.LENGTH_SHORT).show();
        } catch (Exception exception) {
            Toast.makeText(this, "Could not save token: " + exception.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    private void startJarvis() {
        saveSettings();
        List<String> missing = new ArrayList<>();
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            missing.add(Manifest.permission.RECORD_AUDIO);
        }
        if (android.os.Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            missing.add(Manifest.permission.POST_NOTIFICATIONS);
        }
        if (!missing.isEmpty()) {
            requestPermissions(missing.toArray(new String[0]), 1710);
            return;
        }
        if (store.baseUrl().isBlank() || store.token().isBlank()) {
            Toast.makeText(this, "Home Assistant URL and token are required", Toast.LENGTH_LONG).show();
            return;
        }
        startForegroundService(new Intent(this, VoiceService.class).setAction(VoiceService.ACTION_START));
    }

    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != 1710) return;
        for (int result : grantResults) {
            if (result != PackageManager.PERMISSION_GRANTED) {
                Toast.makeText(this, "Microphone permission is required", Toast.LENGTH_LONG).show();
                return;
            }
        }
        startJarvis();
    }

    private EditText field(String hint, boolean password) {
        EditText value = new EditText(this);
        value.setHint(hint);
        value.setHintTextColor(Color.rgb(120, 140, 146));
        value.setTextColor(Color.WHITE);
        value.setSingleLine(true);
        value.setPadding(dp(14), dp(10), dp(14), dp(10));
        value.setBackgroundColor(Color.rgb(17, 23, 27));
        if (password) value.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
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

    private LinearLayout.LayoutParams matchWrap() { return matchWrap(0, 0); }
    private LinearLayout.LayoutParams matchWrap(int top, int bottom) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.topMargin = top;
        params.bottomMargin = bottom;
        return params;
    }
    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
}
