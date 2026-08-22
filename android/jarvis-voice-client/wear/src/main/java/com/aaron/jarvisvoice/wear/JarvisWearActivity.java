package com.aaron.jarvisvoice.wear;

import android.Manifest;
import android.app.Activity;
import android.app.RemoteInput;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.SystemClock;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.util.Log;
import android.text.Spannable;
import android.text.SpannableStringBuilder;
import android.text.style.ForegroundColorSpan;
import android.text.style.StyleSpan;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.widget.ImageButton;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import androidx.core.content.ContextCompat;
import androidx.wear.input.RemoteInputIntentHelper;
import com.aaron.jarvisvoice.R;
import com.aaron.jarvisvoice.protocol.WatchConversationState;
import com.aaron.jarvisvoice.protocol.WearUiMetrics;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** Compact, inset-aware conversation surface designed for round Wear displays. */
public final class JarvisWearActivity extends Activity {
    private static final String TAG = "JarvisWearActivity";
    public static final String EXTRA_AUTO_START = "auto_start";
    private static final int REQUEST_MIC = 7, REQUEST_TEXT = 8;
    private static final String REMOTE_INPUT_KEY = "jarvis_watch_text";
    private record Message(String role, String text) {}

    private final List<Message> messages = new ArrayList<>();
    private final StringBuilder streamingAssistant = new StringBuilder();
    private TextView status, emptyTranscript;
    private LinearLayout transcriptBox;
    private ScrollView transcriptScroll;
    private ImageButton control;
    private FrameLayout controlTarget;
    private boolean active;

    private final BroadcastReceiver receiver = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            String role = intent.getStringExtra(WearVoiceService.EXTRA_ROLE);
            String text = intent.getStringExtra(WearVoiceService.EXTRA_TEXT);
            if (role != null && text != null) {
                updateTranscript(role, text, intent.getBooleanExtra(WearVoiceService.EXTRA_COMPLETE, true));
                return;
            }
            String state = intent.getStringExtra(WearVoiceService.EXTRA_STATE);
            if (state != null) render(!WatchConversationState.IDLE.name().equals(state), state,
                intent.getStringExtra(WearVoiceService.EXTRA_MESSAGE));
        }
    };

    @Override protected void onCreate(Bundle saved) {
        super.onCreate(saved); buildUi();
        ContextCompat.registerReceiver(this, receiver,
            new IntentFilter(WearVoiceService.ACTION_STATE), ContextCompat.RECEIVER_NOT_EXPORTED);
        if (getIntent().getBooleanExtra(EXTRA_AUTO_START, false)
                || Intent.ACTION_ASSIST.equals(getIntent().getAction())) startConversation();
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent); setIntent(intent);
        if (intent.getBooleanExtra(EXTRA_AUTO_START, false)
                || Intent.ACTION_ASSIST.equals(intent.getAction())) startConversation();
    }

    private void buildUi() {
        getWindow().setStatusBarColor(getColor(R.color.jarvis_white));
        getWindow().setNavigationBarColor(getColor(R.color.jarvis_white));
        boolean round = (getResources().getConfiguration().screenLayout
            & Configuration.SCREENLAYOUT_ROUND_MASK) == Configuration.SCREENLAYOUT_ROUND_YES;
        int widthDp = Math.round(getResources().getDisplayMetrics().widthPixels
            / getResources().getDisplayMetrics().density);
        int safeSide = WearUiMetrics.safeSideDp(widthDp, round);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL); root.setGravity(Gravity.CENTER_HORIZONTAL);
        root.setPadding(dp(safeSide), dp(6), dp(safeSide), dp(7));
        root.setBackgroundColor(getColor(R.color.jarvis_white));
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
            int side = Math.max(dp(safeSide), Math.max(bars.left, bars.right));
            view.setPadding(side, Math.max(dp(5), bars.top), side, Math.max(dp(6), bars.bottom));
            return insets;
        });

        TextView title = new TextView(this);
        title.setText("J A R V I S"); title.setTextColor(getColor(R.color.jarvis_black));
        title.setTextSize(14); title.setTypeface(Typeface.create("sans-serif", Typeface.NORMAL));
        title.setLetterSpacing(0.12f); title.setGravity(Gravity.CENTER);
        root.addView(title, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(22)));

        status = new TextView(this); status.setTextSize(10);
        status.setTypeface(Typeface.create("sans-serif", Typeface.NORMAL));
        status.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams statusParams = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(18));
        statusParams.bottomMargin = dp(3); root.addView(status, statusParams);

        transcriptScroll = new ScrollView(this); transcriptScroll.setFillViewport(true);
        transcriptScroll.setVerticalScrollBarEnabled(false);
        transcriptBox = new LinearLayout(this); transcriptBox.setOrientation(LinearLayout.VERTICAL);
        transcriptBox.setGravity(Gravity.CENTER_VERTICAL); transcriptBox.setPadding(dp(2), dp(3), dp(2), dp(3));
        emptyTranscript = new TextView(this); emptyTranscript.setText(R.string.ask_anything);
        emptyTranscript.setTextColor(getColor(R.color.jarvis_muted)); emptyTranscript.setTextSize(11);
        emptyTranscript.setGravity(Gravity.CENTER); transcriptBox.addView(emptyTranscript, new LinearLayout.LayoutParams(-1, -2));
        transcriptScroll.addView(transcriptBox, new ScrollView.LayoutParams(-1, -1));
        LinearLayout.LayoutParams conversation = new LinearLayout.LayoutParams(-1, 0, 1f);
        root.addView(transcriptScroll, conversation);

        LinearLayout actions = new LinearLayout(this); actions.setGravity(Gravity.CENTER);
        actions.setPadding(0, dp(3), 0, 0);
        ImageButton type = actionButton(android.R.drawable.ic_menu_edit, "Type to Jarvis", false);
        type.setOnClickListener(v -> openTextInput());
        actions.addView(touchTarget(type, WearUiMetrics.textActionVisibleDp()),
            new LinearLayout.LayoutParams(dp(WearUiMetrics.actionTouchTargetDp()), dp(WearUiMetrics.actionTouchTargetDp())));
        control = actionButton(R.drawable.ic_mic, "Start conversation", false);
        control.setOnClickListener(v -> { if (active) cancelConversation(); else startConversation(); });
        controlTarget = touchTarget(control, WearUiMetrics.primaryActionVisibleDp());
        LinearLayout.LayoutParams controlParams = new LinearLayout.LayoutParams(dp(46), dp(WearUiMetrics.actionTouchTargetDp()));
        controlParams.leftMargin = dp(6); actions.addView(controlTarget, controlParams);
        root.addView(actions, new LinearLayout.LayoutParams(-1, dp(47)));
        setContentView(root); render(false, WatchConversationState.IDLE.name(), "Ready");
    }

    private ImageButton actionButton(int icon, String description, boolean activeButton) {
        ImageButton button = new ImageButton(this); button.setImageResource(icon);
        button.setPadding(dp(7), dp(7), dp(7), dp(7)); button.setContentDescription(description);
        button.setBackground(circle(activeButton)); return button;
    }

    private FrameLayout touchTarget(ImageButton button, int visibleSizeDp) {
        FrameLayout target = new FrameLayout(this);
        FrameLayout.LayoutParams visible = new FrameLayout.LayoutParams(dp(visibleSizeDp), dp(visibleSizeDp), Gravity.CENTER);
        target.addView(button, visible);
        target.setContentDescription(button.getContentDescription());
        target.setOnClickListener(view -> button.performClick());
        return target;
    }

    private void startConversation() {
        if (active) return;
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQUEST_MIC); return;
        }
        long tappedAt = SystemClock.elapsedRealtime();
        Log.i(TAG, "WATCH_LATENCY ui_tap elapsed_ms=" + tappedAt);
        active = true; render(true, WatchConversationState.LISTENING.name(), "Listening");
        Vibrator vibrator = getSystemService(Vibrator.class);
        if (vibrator != null) vibrator.vibrate(VibrationEffect.createOneShot(40, VibrationEffect.DEFAULT_AMPLITUDE));
        startForegroundService(new Intent(this, WearVoiceService.class).setAction(WearVoiceService.ACTION_START));
    }

    private void cancelConversation() {
        startService(new Intent(this, WearVoiceService.class).setAction(WearVoiceService.ACTION_CANCEL));
        active = false; streamingAssistant.setLength(0);
        render(false, WatchConversationState.IDLE.name(), "Ready");
    }

    private void openTextInput() {
        RemoteInput input = new RemoteInput.Builder(REMOTE_INPUT_KEY).setLabel("Message Jarvis").build();
        Intent intent = RemoteInputIntentHelper.createActionRemoteInputIntent();
        RemoteInputIntentHelper.putRemoteInputsExtra(intent, List.of(input));
        RemoteInputIntentHelper.putTitleExtra(intent, "J A R V I S");
        startActivityForResult(intent, REQUEST_TEXT);
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQUEST_TEXT || resultCode != RESULT_OK || data == null) return;
        Bundle results = RemoteInput.getResultsFromIntent(data);
        CharSequence value = results == null ? null : results.getCharSequence(REMOTE_INPUT_KEY);
        String text = value == null ? "" : value.toString().trim();
        if (text.isEmpty()) return;
        if (!active) startConversation();
        startService(new Intent(this, WearVoiceService.class).setAction(WearVoiceService.ACTION_SEND_TEXT)
            .putExtra(WearVoiceService.EXTRA_TEXT, text));
    }

    private void updateTranscript(String role, String text, boolean complete) {
        if (text.isBlank()) return;
        if ("assistant".equals(role) && !complete) streamingAssistant.append(text);
        else if ("assistant".equals(role)) {
            if (!messages.isEmpty() && "assistant".equals(messages.get(messages.size() - 1).role()))
                messages.set(messages.size() - 1, new Message(role, text));
            else messages.add(new Message(role, text));
            streamingAssistant.setLength(0);
        } else if (messages.isEmpty() || !role.equals(messages.get(messages.size() - 1).role())
                || !text.equals(messages.get(messages.size() - 1).text())) messages.add(new Message(role, text));
        renderTranscript();
    }

    private void renderTranscript() {
        transcriptBox.removeAllViews();
        if (messages.isEmpty() && streamingAssistant.length() == 0) {
            transcriptBox.addView(emptyTranscript, new LinearLayout.LayoutParams(-1, -2));
        } else {
            for (Message message : messages) transcriptBox.addView(messageView(message.role(), message.text()));
            if (streamingAssistant.length() > 0)
                transcriptBox.addView(messageView("assistant", streamingAssistant.toString()));
        }
        transcriptScroll.post(() -> transcriptScroll.fullScroll(View.FOCUS_DOWN));
    }

    private TextView messageView(String role, String text) {
        SpannableStringBuilder value = new SpannableStringBuilder();
        String label = "user".equals(role) ? "YOU" : "JARVIS";
        int start = value.length(); value.append(label);
        value.setSpan(new StyleSpan(Typeface.BOLD), start, value.length(), Spannable.SPAN_EXCLUSIVE_EXCLUSIVE);
        value.setSpan(new ForegroundColorSpan(getColor(R.color.jarvis_muted)), start, value.length(), Spannable.SPAN_EXCLUSIVE_EXCLUSIVE);
        value.append("\n").append(text);
        TextView row = new TextView(this);
        row.setText(value); row.setTextColor(getColor(R.color.jarvis_black)); row.setTextSize(11);
        row.setLineSpacing(0f, 1.05f); row.setPadding(dp(10), dp(6), dp(10), dp(6));
        GradientDrawable card = new GradientDrawable();
        card.setColor(getColor("user".equals(role) ? R.color.jarvis_panel : R.color.jarvis_white));
        card.setCornerRadius(dp(12));
        if (!"user".equals(role)) card.setStroke(dp(1), 0xffe7e7e7);
        row.setBackground(card);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.bottomMargin = dp(4); row.setLayoutParams(params);
        return row;
    }

    private void render(boolean isActive, String state, String message) {
        active = isActive;
        if (!isActive && streamingAssistant.length() > 0) {
            streamingAssistant.setLength(0);
            renderTranscript();
        }
        String label = switch (state) {
            case "LISTENING", "FOLLOW_UP" -> "Listening  •";
            case "PROCESSING" -> "Processing  •";
            case "SPEAKING" -> "Speaking  •";
            default -> "Ready";
        };
        status.setText(message != null && message.toLowerCase(Locale.ROOT).contains("connect") ? "Connecting  •" : label);
        status.setTextColor(getColor(isActive ? R.color.jarvis_black : R.color.jarvis_muted));
        control.setImageResource(isActive ? R.drawable.ic_close : R.drawable.ic_mic);
        control.setBackground(circle(isActive));
        control.setContentDescription(isActive ? "End conversation" : "Start conversation");
        controlTarget.setContentDescription(control.getContentDescription());
    }

    private GradientDrawable circle(boolean activeButton) {
        GradientDrawable shape = new GradientDrawable(); shape.setShape(GradientDrawable.OVAL);
        shape.setColor(getColor(activeButton ? R.color.jarvis_black : R.color.jarvis_panel));
        if (!activeButton) shape.setStroke(dp(1), 0xffd8d8d8); return shape;
    }

    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(request, permissions, results);
        if (request == REQUEST_MIC && results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) startConversation();
    }
    @Override protected void onDestroy() { unregisterReceiver(receiver); super.onDestroy(); }
}
