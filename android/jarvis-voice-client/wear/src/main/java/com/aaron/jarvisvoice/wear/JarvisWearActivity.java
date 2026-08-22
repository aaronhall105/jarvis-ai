package com.aaron.jarvisvoice.wear;

import android.Manifest;
import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.view.Gravity;
import android.widget.ImageButton;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;
import com.aaron.jarvisvoice.R;
import androidx.core.content.ContextCompat;
import com.aaron.jarvisvoice.protocol.WatchConversationState;

public final class JarvisWearActivity extends Activity {
    public static final String EXTRA_AUTO_START = "auto_start";
    private TextView status; private ImageButton control; private boolean active;
    private final BroadcastReceiver receiver = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            String state = intent.getStringExtra(WearVoiceService.EXTRA_STATE);
            render(!WatchConversationState.IDLE.name().equals(state), intent.getStringExtra(WearVoiceService.EXTRA_MESSAGE));
        }
    };
    @Override protected void onCreate(Bundle saved) {
        super.onCreate(saved); buildUi();
        IntentFilter filter = new IntentFilter(WearVoiceService.ACTION_STATE);
        ContextCompat.registerReceiver(
            this,
            receiver,
            filter,
            ContextCompat.RECEIVER_NOT_EXPORTED
        );
        if (getIntent().getBooleanExtra(EXTRA_AUTO_START, false) || Intent.ACTION_ASSIST.equals(getIntent().getAction())) startConversation();
    }
    @Override protected void onNewIntent(Intent intent) { super.onNewIntent(intent); setIntent(intent); if (intent.getBooleanExtra(EXTRA_AUTO_START, false) || Intent.ACTION_ASSIST.equals(intent.getAction())) startConversation(); }
    private void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        root.setPadding(dp(28), dp(20), dp(28), dp(20));
        root.setBackgroundColor(getColor(R.color.jarvis_white));

        ImageView mark = new ImageView(this);
        mark.setImageResource(R.drawable.ic_launcher_foreground);
        root.addView(mark, new LinearLayout.LayoutParams(dp(54), dp(54)));

        TextView title = new TextView(this);
        title.setText(R.string.app_name);
        title.setTextColor(getColor(R.color.jarvis_black));
        title.setTextSize(18);
        title.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        title.setLetterSpacing(0.14f);
        title.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams titleParams = new LinearLayout.LayoutParams(-2, -2);
        titleParams.topMargin = dp(2);
        root.addView(title, titleParams);

        status = new TextView(this);
        status.setTextColor(getColor(R.color.jarvis_muted));
        status.setTextSize(15);
        status.setGravity(Gravity.CENTER);
        status.setMaxLines(2);
        LinearLayout.LayoutParams statusParams = new LinearLayout.LayoutParams(-1, dp(48));
        statusParams.topMargin = dp(4);
        root.addView(status, statusParams);

        control = new ImageButton(this);
        control.setPadding(dp(20), dp(20), dp(20), dp(20));
        control.setElevation(dp(2));
        control.setOnClickListener(v -> { if (active) cancelConversation(); else startConversation(); });
        root.addView(control, new LinearLayout.LayoutParams(dp(76), dp(76)));
        setContentView(root);
        render(false, "Tap to talk");
    }
    private void startConversation() {
        if (active) return;
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) { requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, 7); return; }
        active = true; render(true, "Connecting…"); Vibrator vibrator = getSystemService(Vibrator.class); if (vibrator != null) vibrator.vibrate(VibrationEffect.createOneShot(50, VibrationEffect.DEFAULT_AMPLITUDE));
        startForegroundService(new Intent(this, WearVoiceService.class).setAction(WearVoiceService.ACTION_START));
    }
    private void cancelConversation() { startService(new Intent(this, WearVoiceService.class).setAction(WearVoiceService.ACTION_CANCEL)); active = false; render(false, "Tap to talk"); }
    private void render(boolean isActive, String message) {
        active = isActive;
        status.setText(message == null || message.isBlank() ? (isActive ? "Listening…" : "Tap to talk") : message);
        status.setTextColor(getColor(isActive ? R.color.jarvis_black : R.color.jarvis_muted));
        control.setImageResource(isActive ? R.drawable.ic_close : R.drawable.ic_mic);
        GradientDrawable circle = new GradientDrawable();
        circle.setShape(GradientDrawable.OVAL);
        circle.setColor(getColor(isActive ? R.color.jarvis_black : R.color.jarvis_panel));
        if (!isActive) circle.setStroke(dp(1), 0xffdedede);
        control.setBackground(circle);
        control.setContentDescription(isActive ? "End conversation" : "Start conversation");
    }
    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] results) { super.onRequestPermissionsResult(request, permissions, results); if (request == 7 && results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) startConversation(); }
    @Override protected void onDestroy() { unregisterReceiver(receiver); super.onDestroy(); }
}
