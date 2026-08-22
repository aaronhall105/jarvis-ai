package com.aaron.jarvisvoice.wear;

import android.Manifest;
import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.os.Bundle;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import com.aaron.jarvisvoice.R;
import androidx.core.content.ContextCompat;
import com.aaron.jarvisvoice.protocol.WatchConversationState;

public final class JarvisWearActivity extends Activity {
    public static final String EXTRA_AUTO_START = "auto_start";
    private TextView status; private Button control; private boolean active;
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
        LinearLayout root = new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setGravity(Gravity.CENTER); root.setPadding(28, 28, 28, 28); root.setBackgroundColor(Color.BLACK);
        TextView title = new TextView(this); title.setText(R.string.app_name); title.setTextColor(Color.WHITE); title.setTextSize(22); title.setGravity(Gravity.CENTER);
        status = new TextView(this); status.setTextColor(0xff80deea); status.setTextSize(18); status.setGravity(Gravity.CENTER); status.setPadding(0, 12, 0, 18);
        control = new Button(this); control.setTextSize(28); control.setMinWidth(96); control.setMinHeight(96); control.setOnClickListener(v -> { if (active) cancelConversation(); else startConversation(); });
        root.addView(title); root.addView(status); root.addView(control); setContentView(root); render(false, "Tap to talk");
    }
    private void startConversation() {
        if (active) return;
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) { requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, 7); return; }
        active = true; render(true, "Connecting…"); Vibrator vibrator = getSystemService(Vibrator.class); if (vibrator != null) vibrator.vibrate(VibrationEffect.createOneShot(50, VibrationEffect.DEFAULT_AMPLITUDE));
        startForegroundService(new Intent(this, WearVoiceService.class).setAction(WearVoiceService.ACTION_START));
    }
    private void cancelConversation() { startService(new Intent(this, WearVoiceService.class).setAction(WearVoiceService.ACTION_CANCEL)); active = false; render(false, "Tap to talk"); }
    private void render(boolean isActive, String message) { active = isActive; status.setText(message == null || message.isBlank() ? (isActive ? "Listening…" : "Tap to talk") : message); control.setText(isActive ? "✕" : "🎙"); control.setContentDescription(isActive ? "End conversation" : "Start conversation"); }
    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] results) { super.onRequestPermissionsResult(request, permissions, results); if (request == 7 && results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) startConversation(); }
    @Override protected void onDestroy() { unregisterReceiver(receiver); super.onDestroy(); }
}
