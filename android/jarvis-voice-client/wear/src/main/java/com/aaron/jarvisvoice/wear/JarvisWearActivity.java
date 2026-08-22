package com.aaron.jarvisvoice.wear;

import android.Manifest;
import android.app.Activity;
import android.app.RemoteInput;
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
import android.text.Spannable;
import android.text.SpannableStringBuilder;
import android.text.style.ForegroundColorSpan;
import android.text.style.StyleSpan;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.widget.ImageButton;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import androidx.core.content.ContextCompat;
import androidx.wear.input.RemoteInputIntentHelper;
import com.aaron.jarvisvoice.R;
import com.aaron.jarvisvoice.protocol.WatchConversationState;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** Compact, inset-aware conversation surface designed for round Wear displays. */
public final class JarvisWearActivity extends Activity {
    public static final String EXTRA_AUTO_START = "auto_start";
    private static final int REQUEST_MIC = 7, REQUEST_TEXT = 8;
    private static final String REMOTE_INPUT_KEY = "jarvis_watch_text";
    private record Message(String role, String text) {}

    private final List<Message> messages = new ArrayList<>();
    private final StringBuilder streamingAssistant = new StringBuilder();
    private TextView status, transcript, emptyTranscript;
    private ScrollView transcriptScroll;
    private ImageButton control;
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
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL); root.setGravity(Gravity.CENTER_HORIZONTAL);
        root.setPadding(dp(18), dp(8), dp(18), dp(10));
        root.setBackgroundColor(getColor(R.color.jarvis_white));
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
            int side = Math.max(dp(14), Math.max(bars.left, bars.right));
            view.setPadding(side, Math.max(dp(6), bars.top), side, Math.max(dp(8), bars.bottom));
            return insets;
        });

        TextView title = new TextView(this);
        title.setText("J A R V I S"); title.setTextColor(getColor(R.color.jarvis_black));
        title.setTextSize(15); title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        title.setLetterSpacing(0.12f); title.setGravity(Gravity.CENTER);
        root.addView(title, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(26)));

        status = new TextView(this); status.setTextSize(11);
        status.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        status.setGravity(Gravity.CENTER); status.setAllCaps(true);
        LinearLayout.LayoutParams statusParams = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(24));
        statusParams.bottomMargin = dp(2); root.addView(status, statusParams);

        transcriptScroll = new ScrollView(this); transcriptScroll.setFillViewport(true);
        transcriptScroll.setVerticalScrollBarEnabled(false);
        GradientDrawable panel = new GradientDrawable(); panel.setColor(getColor(R.color.jarvis_panel));
        panel.setCornerRadius(dp(14)); transcriptScroll.setBackground(panel);
        LinearLayout box = new LinearLayout(this); box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER_VERTICAL); box.setPadding(dp(12), dp(8), dp(12), dp(8));
        emptyTranscript = new TextView(this); emptyTranscript.setText(R.string.ask_anything);
        emptyTranscript.setTextColor(getColor(R.color.jarvis_muted)); emptyTranscript.setTextSize(12);
        emptyTranscript.setGravity(Gravity.CENTER); box.addView(emptyTranscript, new LinearLayout.LayoutParams(-1, -2));
        transcript = new TextView(this); transcript.setTextColor(getColor(R.color.jarvis_black));
        transcript.setTextSize(12); transcript.setLineSpacing(0f, 1.08f);
        box.addView(transcript, new LinearLayout.LayoutParams(-1, -2));
        transcriptScroll.addView(box, new ScrollView.LayoutParams(-1, -1));
        LinearLayout.LayoutParams conversation = new LinearLayout.LayoutParams(-1, 0, 1f);
        conversation.leftMargin = dp(4); conversation.rightMargin = dp(4); root.addView(transcriptScroll, conversation);

        LinearLayout actions = new LinearLayout(this); actions.setGravity(Gravity.CENTER);
        actions.setPadding(0, dp(5), 0, 0);
        ImageButton type = actionButton(android.R.drawable.ic_menu_edit, "Type to Jarvis");
        type.setOnClickListener(v -> openTextInput()); actions.addView(type, new LinearLayout.LayoutParams(dp(42), dp(42)));
        control = actionButton(R.drawable.ic_mic, "Start conversation");
        control.setOnClickListener(v -> { if (active) cancelConversation(); else startConversation(); });
        LinearLayout.LayoutParams controlParams = new LinearLayout.LayoutParams(dp(46), dp(46));
        controlParams.leftMargin = dp(10); actions.addView(control, controlParams);
        root.addView(actions, new LinearLayout.LayoutParams(-1, dp(51)));
        setContentView(root); render(false, WatchConversationState.IDLE.name(), "Ready");
    }

    private ImageButton actionButton(int icon, String description) {
        ImageButton button = new ImageButton(this); button.setImageResource(icon);
        button.setPadding(dp(11), dp(11), dp(11), dp(11)); button.setContentDescription(description);
        button.setBackground(circle(false)); return button;
    }

    private void startConversation() {
        if (active) return;
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQUEST_MIC); return;
        }
        active = true; render(true, WatchConversationState.LISTENING.name(), "Connecting");
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
        SpannableStringBuilder value = new SpannableStringBuilder();
        for (Message message : messages) appendMessage(value, message.role(), message.text());
        if (streamingAssistant.length() > 0) appendMessage(value, "assistant", streamingAssistant.toString());
        transcript.setText(value); emptyTranscript.setVisibility(value.length() == 0 ? View.VISIBLE : View.GONE);
        transcriptScroll.post(() -> transcriptScroll.fullScroll(View.FOCUS_DOWN));
    }

    private void appendMessage(SpannableStringBuilder value, String role, String text) {
        if (value.length() > 0) value.append("\n\n");
        String label = "user".equals(role) ? "YOU  " : "JARVIS  ";
        int start = value.length(); value.append(label);
        value.setSpan(new StyleSpan(Typeface.BOLD), start, value.length(), Spannable.SPAN_EXCLUSIVE_EXCLUSIVE);
        value.setSpan(new ForegroundColorSpan(getColor(R.color.jarvis_muted)), start, value.length(), Spannable.SPAN_EXCLUSIVE_EXCLUSIVE);
        value.append(text);
    }

    private void render(boolean isActive, String state, String message) {
        active = isActive;
        if (!isActive && streamingAssistant.length() > 0) {
            streamingAssistant.setLength(0);
            renderTranscript();
        }
        String label = switch (state) {
            case "LISTENING", "FOLLOW_UP" -> "LISTENING";
            case "PROCESSING" -> "PROCESSING";
            case "SPEAKING" -> "SPEAKING";
            default -> "READY";
        };
        status.setText(message != null && message.toLowerCase(Locale.ROOT).contains("connect") ? "CONNECTING" : label);
        status.setTextColor(getColor(isActive ? R.color.jarvis_black : R.color.jarvis_muted));
        control.setImageResource(isActive ? R.drawable.ic_close : R.drawable.ic_mic);
        control.setBackground(circle(isActive));
        control.setContentDescription(isActive ? "End conversation" : "Start conversation");
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
