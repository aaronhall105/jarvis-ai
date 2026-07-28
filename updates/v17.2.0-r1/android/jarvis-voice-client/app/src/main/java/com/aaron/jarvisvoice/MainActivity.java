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
    private EditText coreUrl;
    private EditText mobileToken;
    private EditText userName;
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

        TextView title = text("JARVIS REALTIME", 28, Color.rgb(0, 229, 255));
        title.setGravity(Gravity.CENTER_HORIZONTAL);
        panel.addView(title, matchWrap());
        TextView subtitle = text("Android client v17.2.0", 14, Color.rgb(165, 181, 186));
        subtitle.setGravity(Gravity.CENTER_HORIZONTAL);
        panel.addView(subtitle, matchWrap(0, dp(24)));

        TextView section = text("JARVIS CORE CONNECTION", 13, Color.rgb(0, 229, 255));
        panel.addView(section, matchWrap(0, dp(8)));

        coreUrl = field("Jarvis Core URL — for example http://192.168.1.40:8000", false);
        mobileToken = field("Mobile voice token from the v17.2.0 installer", true);
        userName = field("Your name", false);
        panel.addView(coreUrl, matchWrap(0, dp(10)));
        panel.addView(mobileToken, matchWrap(0, dp(10)));
        panel.addView(userName, matchWrap(0, dp(18)));

        Button save = button("SAVE SETTINGS");
        save.setOnClickListener(view -> saveSettings());
        panel.addView(save, matchWrap(0, dp(10)));

        Button start = button("START LIVE VOICE");
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
            "Tap Start once, then talk naturally. Audio streams continuously through Jarvis Core and GPT Realtime with semantic turn detection. You can speak over Jarvis to interrupt. This phase focuses on ChatGPT-style live conversation; the local always-on “Jarvis” wake word is the next isolated phase.",
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
        if (store.hasMobileToken()) {
            mobileToken.setHint("Mobile voice token saved securely — leave blank to keep it");
        }
    }

    private void saveSettings() {
        try {
            store.saveRealtime(
                coreUrl.getText().toString(),
                mobileToken.getText().toString(),
                userName.getText().toString()
            );
            mobileToken.setText("");
            mobileToken.setHint("Mobile voice token saved securely — leave blank to keep it");
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
            requestPermissions(missing.toArray(new String[0]), 1720);
            return;
        }
        if (store.coreUrl().isBlank() || store.mobileToken().isBlank()) {
            Toast.makeText(this, "Jarvis Core URL and mobile voice token are required", Toast.LENGTH_LONG).show();
            return;
        }
        startForegroundService(new Intent(this, VoiceService.class).setAction(VoiceService.ACTION_START));
    }

    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != 1720) return;
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
