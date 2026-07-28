package com.aaron.jarvisvoice;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.service.voice.VoiceInteractionSession;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.EditorInfo;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Compact system-assistant overlay shown above the current app. */
public final class JarvisVoiceInteractionSession extends VoiceInteractionSession {
    private static final int BLACK = Color.rgb(22, 22, 22);
    private static final int MID = Color.rgb(104, 104, 104);
    private static final int LINE = Color.rgb(226, 226, 226);
    private static final int SOFT = Color.rgb(246, 246, 246);
    private static final int WHITE = Color.WHITE;

    private final Context context;
    private TextView status;
    private TextView transcript;
    private EditText composer;
    private Button micButton;
    private boolean voiceActive;
    private boolean listening;
    private StringBuilder streamed = new StringBuilder();

    private final BroadcastReceiver receiver = new BroadcastReceiver() {
        @Override public void onReceive(Context ignored, Intent intent) {
            if (!VoiceService.ACTION_EVENT.equals(intent.getAction())) return;
            String event = safe(intent.getStringExtra(VoiceService.EXTRA_EVENT));
            String role = safe(intent.getStringExtra(VoiceService.EXTRA_ROLE));
            String text = safe(intent.getStringExtra(VoiceService.EXTRA_TEXT));
            voiceActive = intent.getBooleanExtra(VoiceService.EXTRA_ACTIVE, voiceActive);
            listening = intent.getBooleanExtra(VoiceService.EXTRA_LISTENING, listening);
            updateMicButton();
            switch (event) {
                case "status" -> status.setText(text);
                case "thinking" -> {
                    streamed.setLength(0);
                    transcript.setText("Thinking…");
                    transcript.setTextColor(MID);
                }
                case "assistant_delta" -> {
                    streamed.append(text);
                    transcript.setText(streamed.toString());
                    transcript.setTextColor(BLACK);
                }
                case "message" -> {
                    if (ChatMessage.ASSISTANT.equals(role)) {
                        streamed.setLength(0);
                        transcript.setText(text);
                        transcript.setTextColor(BLACK);
                    } else if (ChatMessage.USER.equals(role)) {
                        status.setText("You: " + text);
                    }
                }
                case "draft" -> status.setText(text.isBlank() ? "Listening" : text);
                case "error" -> {
                    status.setText("Error");
                    transcript.setText(text);
                    transcript.setTextColor(BLACK);
                }
                default -> { }
            }
        }
    };

    public JarvisVoiceInteractionSession(Context context) {
        super(context);
        this.context = context;
        setTheme(R.style.Theme_JarvisAssistantOverlay);
    }

    @Override public void onCreate() {
        super.onCreate();
        IntentFilter filter = new IntentFilter(VoiceService.ACTION_EVENT);
        if (android.os.Build.VERSION.SDK_INT >= 33) {
            context.registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            context.registerReceiver(receiver, filter);
        }
    }

    @Override public View onCreateContentView() {
        FrameLayout root = new FrameLayout(context);
        root.setBackgroundColor(Color.TRANSPARENT);
        root.setPadding(dp(12), dp(12), dp(12), dp(18));
        root.setOnClickListener(view -> finish());

        LinearLayout card = new LinearLayout(context);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(18), dp(14), dp(18), dp(14));
        card.setBackground(rounded(WHITE, 24, 1, LINE));
        card.setElevation(dp(14));
        card.setOnClickListener(view -> { });

        LinearLayout header = new LinearLayout(context);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        TextView title = text("Jarvis", 19, BLACK);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        header.addView(title, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        Button open = textButton("Open chat");
        open.setOnClickListener(view -> openFullChat());
        header.addView(open, wrapWrap(0, dp(4)));
        Button close = textButton("Close");
        close.setOnClickListener(view -> finish());
        header.addView(close, wrapWrap());
        card.addView(header, matchWrap());

        status = text("Starting…", 12, MID);
        status.setPadding(0, dp(5), 0, dp(8));
        card.addView(status, matchWrap());

        transcript = text("How can I help?", 18, BLACK);
        transcript.setLineSpacing(0f, 1.12f);
        transcript.setMinHeight(dp(70));
        transcript.setMaxLines(6);
        transcript.setPadding(0, dp(8), 0, dp(12));
        card.addView(transcript, matchWrap());

        LinearLayout composerRow = new LinearLayout(context);
        composerRow.setOrientation(LinearLayout.HORIZONTAL);
        composerRow.setGravity(Gravity.CENTER_VERTICAL);
        composerRow.setPadding(dp(10), dp(6), dp(6), dp(6));
        composerRow.setBackground(rounded(SOFT, 22, 0, Color.TRANSPARENT));

        composer = new EditText(context);
        composer.setHint("Message Jarvis");
        composer.setHintTextColor(Color.rgb(135, 135, 135));
        composer.setTextColor(BLACK);
        composer.setTextSize(15);
        composer.setSingleLine(true);
        composer.setBackgroundColor(Color.TRANSPARENT);
        composer.setImeOptions(EditorInfo.IME_ACTION_SEND);
        composer.setOnEditorActionListener((view, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_SEND) {
                sendText();
                return true;
            }
            return false;
        });
        composerRow.addView(composer, new LinearLayout.LayoutParams(0, dp(42), 1f));

        Button send = circleButton("↑");
        send.setOnClickListener(view -> sendText());
        composerRow.addView(send, new LinearLayout.LayoutParams(dp(40), dp(40)));
        card.addView(composerRow, matchWrap());

        micButton = actionButton("Listening");
        micButton.setOnClickListener(view -> toggleVoice());
        card.addView(micButton, matchWrap(dp(10), 0));

        FrameLayout.LayoutParams cardParams = new FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
            Gravity.BOTTOM
        );
        root.addView(card, cardParams);
        return root;
    }

    @Override public void onShow(Bundle args, int showFlags) {
        super.onShow(args, showFlags);
        setKeepAwake(true);
        String command = args == null ? "" : safe(args.getString(JarvisVoiceInteractionService.ARG_COMMAND));
        Intent invoke = new Intent(context, VoiceService.class)
            .setAction(VoiceService.ACTION_ASSISTANT_INVOKE)
            .putExtra(VoiceService.EXTRA_TEXT, command);
        context.startForegroundService(invoke);
    }

    @Override public void onHide() {
        setKeepAwake(false);
        try {
            context.startService(new Intent(context, VoiceService.class).setAction(VoiceService.ACTION_ASSISTANT_DISMISS));
        } catch (Exception ignored) {}
        JarvisVoiceInteractionService.rearmWakeIfActive(context);
        super.onHide();
    }

    @Override public void onDestroy() {
        try { context.unregisterReceiver(receiver); } catch (Exception ignored) {}
        super.onDestroy();
    }

    private void sendText() {
        String value = composer.getText().toString().trim();
        if (value.isEmpty()) return;
        composer.setText("");
        context.startForegroundService(
            new Intent(context, VoiceService.class)
                .setAction(VoiceService.ACTION_SEND_TEXT)
                .putExtra(VoiceService.EXTRA_TEXT, value)
        );
    }

    private void toggleVoice() {
        String action = voiceActive ? VoiceService.ACTION_STOP_VOICE : VoiceService.ACTION_START_VOICE;
        context.startForegroundService(new Intent(context, VoiceService.class).setAction(action));
    }

    private void openFullChat() {
        Intent open = new Intent(context, MainActivity.class)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        context.startActivity(open);
        finish();
    }

    private void updateMicButton() {
        if (micButton == null) return;
        micButton.setText(voiceActive ? (listening ? "Listening · tap to end" : "End voice") : "Start voice");
    }

    private Button textButton(String label) {
        Button value = new Button(context);
        value.setText(label);
        value.setTextSize(12);
        value.setTextColor(BLACK);
        value.setAllCaps(false);
        value.setMinWidth(0);
        value.setMinimumWidth(0);
        value.setMinHeight(0);
        value.setMinimumHeight(0);
        value.setPadding(dp(8), dp(5), dp(8), dp(5));
        value.setBackgroundColor(Color.TRANSPARENT);
        return value;
    }

    private Button actionButton(String label) {
        Button value = new Button(context);
        value.setText(label);
        value.setTextSize(14);
        value.setTextColor(WHITE);
        value.setAllCaps(false);
        value.setPadding(dp(14), dp(10), dp(14), dp(10));
        value.setBackground(rounded(BLACK, 20, 0, Color.TRANSPARENT));
        return value;
    }

    private Button circleButton(String label) {
        Button value = new Button(context);
        value.setText(label);
        value.setTextSize(21);
        value.setTextColor(WHITE);
        value.setAllCaps(false);
        value.setMinWidth(0);
        value.setMinimumWidth(0);
        value.setMinHeight(0);
        value.setMinimumHeight(0);
        value.setGravity(Gravity.CENTER);
        value.setBackground(rounded(BLACK, 20, 0, Color.TRANSPARENT));
        return value;
    }

    private TextView text(String value, int size, int colour) {
        TextView view = new TextView(context);
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

    private LinearLayout.LayoutParams matchWrap() { return matchWrap(0, 0); }
    private LinearLayout.LayoutParams matchWrap(int top, int bottom) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        );
        params.topMargin = top;
        params.bottomMargin = bottom;
        return params;
    }

    private LinearLayout.LayoutParams wrapWrap() { return wrapWrap(0, 0); }
    private LinearLayout.LayoutParams wrapWrap(int top, int right) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        );
        params.topMargin = top;
        params.rightMargin = right;
        return params;
    }

    private int dp(int value) {
        return Math.round(value * context.getResources().getDisplayMetrics().density);
    }

    private static String safe(String value) { return value == null ? "" : value; }
}
